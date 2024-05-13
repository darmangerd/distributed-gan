import datetime
import torch.distributed as dist
import torch
import logging
import torch.nn as nn
import torch.utils.data
from tensordict import TensorDict
import numpy as np
from threading import Thread
from pathlib import Path
from dataloaders.DataPartitioner import DataPartitioner
from typing import List, Dict, Any, Tuple
import os
import json
import time
from pathlib import Path
import os
from torchvision.utils import make_grid
from torchvision.transforms.functional import to_pil_image

def swap_event(model: torch.nn.Module, rank: int, swap_status: Dict[str, Any]) -> None:
    while True:
        if swap_status["stop"]:
            break
        try:
            logging.info(f"Worker {rank} waiting for state_dict")
            old_state_dict = dict(model.state_dict())
            for k, v in old_state_dict.items():
                old_state_dict[k] = v.cpu()
            new_state_dict: TensorDict = TensorDict(
                old_state_dict, batch_size=[]
            ).unflatten_keys(".")
            new_state_dict.recv(src=rank, init_tag=2)
            old_state_dict_tensor: TensorDict = TensorDict(
                old_state_dict, batch_size=[]
            ).unflatten_keys(".")
            old_state_dict_tensor.send(dst=rank, init_tag=2)
            swap_status["rank"] = rank
            swap_status["state_dict"] = dict(new_state_dict.flatten_keys("."))
        except Exception as e:
            logging.error(f"Worker {rank} failed to swap state_dict: {e}")
            continue

def worker(
    backend: str,
    rank: int,
    world_size: int,
    data_partitioner: DataPartitioner,
    discriminator_lr: float,
    generator_lr: float,
    epochs: int,
    swap_interval: int,
    local_epochs: int,
    log_interval: int,
    discriminator: torch.nn.Module,
    generator: torch.nn.Module,
    batch_size: int,
    image_shape: Tuple[int, int, int],
    log_folder: Path,
    device: torch.device = torch.device("cpu"),
    z_dim: int = 100,
) -> None:
    print(
        f"Worker {rank} starting",
        os.environ.get("MASTER_ADDR"),
        os.environ.get("MASTER_PORT"),
    )
    dist.init_process_group(
        backend=backend,
        rank=rank,
        world_size=world_size,
        timeout=datetime.timedelta(weeks=52),
    )
    logging.info(f"Worker {rank} initialized on device {device}")

    logs_file = log_folder / f"worker_{rank}.logs.json"
    logs = {}

    indices_size = torch.zeros(1, dtype=torch.int, device=torch.device("cpu"))
    dist.recv(tensor=indices_size, src=0, tag=4)
    logging.info(f"Worker will store {indices_size.item()} entries")
    indices = torch.arange(indices_size.item(), device=torch.device("cpu"))
    logging.info(f"Worker {rank} waiting for indices with shape {indices.shape}")
    dist.recv(tensor=indices, src=0, tag=4)

    partition_train = data_partitioner.get_subset_from_indices(indices, train=True)

    g = torch.Generator()
    g.manual_seed(0)
    dataloader = torch.utils.data.DataLoader(
        partition_train,
        batch_size=batch_size,
        shuffle=True,
        generator=g,
    )

    logging.info(
        f"Worker {rank} with length {len(partition_train)} out of {len(data_partitioner.train_dataset)} ({indices})"
    )

    criterion = nn.BCEWithLogitsLoss()
    optimizer_discriminator = torch.optim.Adam(
        discriminator.parameters(), lr=discriminator_lr, betas=(0, 0.999)
    )

    other_workers_rank = list(range(1, world_size))
    other_workers_rank.remove(rank)

    swap_status = {"rank": -1, "state_dict": None, "stop": False}
    threads: List[Thread] = []
    for other_worker in other_workers_rank:
        t = Thread(target=swap_event, args=(discriminator, other_worker, swap_status))
        t.start()
        threads.append(t)

    real_labels = torch.ones(batch_size).to(device)
    fake_labels = torch.zeros(batch_size).to(device)
    for epoch in range(epochs):
        logs[epoch] = {}
        logging.info(f"Worker {rank} starting epoch {epoch}")

        # Swap state_dict if needed with another worker
        if swap_status["rank"] != -1:
            swap_with = swap_status["rank"]
            logging.info(f"Worker {rank} swapping state_dict with worker {swap_with}")
            discriminator.load_state_dict(swap_status["state_dict"])
            discriminator = discriminator.to(device)
            swap_status["rank"] = -1
            swap_status["state_dict"] = None
            logging.info(
                f"Worker {rank} finished swapping state_dict with worker {swap_with}"
            )

        # Get N random samples from the dataset
        real_images = next(iter(dataloader))[0].to(device)

        # Save real images
        # grid = make_grid(
        #     real_images, nrow=4, normalize=True, value_range=(-1, 1), padding=0
        # )
        # grid_pil = to_pil_image(grid.cpu())
        # grid_path = Path(f"saved_images_worker_{rank}")
        # grid_path.mkdir(parents=True, exist_ok=True)
        # grid_path = grid_path / f"real_epoch_{epoch}.png"
        # grid_pil.save(grid_path)

        # Receive fake images from the server
        X_gen = torch.zeros((2 * batch_size, *image_shape), dtype=torch.float32)
        dist.recv(tensor=X_gen, src=0, tag=1)
        X_gen = X_gen.to(device)
        X_n = X_gen[:batch_size]
        X_g = X_gen[batch_size:]
        X_n.requires_grad = True
        X_g.requires_grad = True
        logging.info(f"Worker {rank} received data of shape {X_gen.shape}")

        discriminator.train()
        start_time = time.time()
        for l in range(local_epochs):
            start_time_local = time.time()

            # Train Discriminator with real images
            discriminator.zero_grad()
            output: torch.Tensor = discriminator(real_images)
            d_loss_real: torch.Tensor = criterion(output, real_labels)

            # Train Discriminator with fake images
            output = discriminator(X_n.detach())
            d_loss_fake: torch.Tensor = criterion(output, fake_labels)
            d_loss = d_loss_real + d_loss_fake
            d_loss.backward()
            optimizer_discriminator.step()

            if l % log_interval == 0 or l == local_epochs - 1:
                end_time_local = time.time()
                logs[epoch][l] = {
                    "d_loss_real": d_loss_real.item(),
                    "d_loss_fake": d_loss_fake.item(),
                    "d_total_loss": (d_loss_real + d_loss_fake).item(),
                    "elapsed_time": end_time_local - start_time_local,
                    "start_time": start_time_local,
                    "end_time": end_time_local,
                }

                with open(logs_file, "w") as f:
                    json.dump(logs, f)

            logging.info(
                f"Worker {rank} finished local iteration {l}, discriminator loss {d_loss_real + d_loss_fake}"
            )

        logs[epoch]["elapsed_time"] = time.time() - start_time

        logs[epoch]["start_time_send"] = time.time()
        # Compute output of the discriminator for a given input
        d_loss_eval: torch.Tensor = discriminator(X_g)
        # Compute loss for fake data
        loss_gen: torch.Tensor = criterion(
            d_loss_eval,
            real_labels,
        )
        # Compute gradients
        loss_gen.backward()
        logging.info(
            f"Worker {rank} sending gradients to server with shape {X_g.grad.shape}"
        )
        # Send the gradients to the server
        dist.send(tensor=X_g.grad.clone().cpu(), dst=0, tag=3)
        logs[epoch]["end_time_send"] = time.time()

        if len(other_workers_rank) > 0:
            if epoch % int(len(partition_train) * swap_interval / batch_size) == 0 and epoch > 0:
                logs[epoch]["start_time_swap"] = time.time()
                # pick a random worker to swap with
                swap_with = np.random.choice(other_workers_rank)
                logging.info(f"Worker {rank} picked worker {swap_with} to swap with")

                # Send the state_dict to the other worker
                state_dict = discriminator.cpu().state_dict()
                current_state_dict: TensorDict = TensorDict(
                    state_dict, batch_size=[]
                ).unflatten_keys(".")
                current_state_dict.send(dst=swap_with, init_tag=2)

                # Receive the state_dict from the other worker
                state_dict: TensorDict = TensorDict(
                    state_dict, batch_size=[]
                ).unflatten_keys(".")
                state_dict.recv(src=swap_with, init_tag=2)

                # Swap state_dict
                discriminator.load_state_dict(dict(state_dict.flatten_keys(".")))
                discriminator = discriminator.to(device)
                logs[epoch]["end_time_swap"] = time.time()

        with open(logs_file, "w") as f:
            json.dump(logs, f)

    # Save the model
    model_path = Path(f"saved_models/worker_{rank}")
    model_path.mkdir(parents=True, exist_ok=True)
    model_path = model_path / "discriminator.pth"
    torch.save(discriminator.state_dict(), model_path)
    logging.info(f"Worker {rank} saved model to {model_path}")

    # Stop the swap threads
    swap_status["stop"] = True
    for t in threads:
        t.join()

    # Discard the process group
    dist.destroy_process_group()

    logging.info(f"Worker {rank} finished training")

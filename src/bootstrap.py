import torch
import logging
import argparse
import os
from typing import Tuple, Dict, Any
from dataloaders.DataPartitioner import DataPartitioner
from pathlib import Path
import torch.nn as nn
import random
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

parser = argparse.ArgumentParser()
parser.add_argument("--name", type=str, default="Master")
parser.add_argument("--backend", type=str, default="nccl")
parser.add_argument("--port", type=int, default=12345)
parser.add_argument("--world_size", type=int, default=2)
parser.add_argument("--dataset", type=str, default="cifar")
parser.add_argument("--rank", type=int, default=0)
parser.add_argument("--epochs", type=int, default=10)
parser.add_argument("--swap_interval", type=int, default=1)
parser.add_argument("--local_epochs", type=int, default=10)
parser.add_argument("--model", type=str, default="cifar")
parser.add_argument("--batch_size", type=int, default=32)
parser.add_argument("--log_interval", type=int, default=10)
parser.add_argument("--n_samples_fid", type=int, default=10)
parser.add_argument("--generator_lr", type=float, default=0.001)
parser.add_argument("--discriminator_lr", type=float, default=0.004)
parser.add_argument("--device", type=str, default="cpu")
parser.add_argument("--master_addr", type=str, default="localhost")
parser.add_argument("--master_port", type=str, default="1234")
parser.add_argument("--iid", type=int, default=1)
args = parser.parse_args()

def verify_imports(imports_options: Dict[str, Any], chosen: str) -> None:
    if chosen.lower() not in imports_options:
        raise ValueError(
            f"Option \"{args.dataset}\" not available. Choose from {imports_options.keys()}"
        )

def weights_init(m: nn.Module) -> None:
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


if __name__ == "__main__":
    os.environ["MASTER_ADDR"] = args.master_addr
    os.environ["MASTER_PORT"] = args.master_port
    os.environ["WORLD_SIZE"] = str(args.world_size)
    os.environ["RANK"] = str(args.rank)
    os.environ["GLOO_SOCKET_IFNAME"] = "en0"
    os.environ["USE_CUDA"] = "0"

    os.environ['GLOO_LOG_LEVEL'] = 'DEBUG'
    os.environ["TORCH_CPP_LOG_LEVEL"]="INFO"
    os.environ[
        "TORCH_DISTRIBUTED_DEBUG"
    ] = "DETAIL"
    os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

    log_folder: Path = Path("logs")
    log_folder.mkdir(parents=True, exist_ok=True)

    from dataloaders.Cifar10Partitioner import Cifar10Partitioner, cifar10_shape
    from dataloaders.MnistPartitioner import MnistPartitioner, mnist_shape
    from dataloaders.CelebaPartitioner import CelebaPartitioner, celeba_shape

    available_datasets: Dict[str, Tuple[DataPartitioner, int]] = {
        "cifar": (Cifar10Partitioner, cifar10_shape),
        "mnist": (MnistPartitioner, mnist_shape),
        "celeba": (CelebaPartitioner, celeba_shape),
    }
    verify_imports(available_datasets, args.dataset)
    dataset: DataPartitioner = available_datasets[args.dataset][0](args.world_size - 1, args.rank)
    image_shape = available_datasets[args.dataset][1]
    dataset.load_data()

    from models.CifarDiscriminator import CifarDiscriminator
    from models.MnistDiscriminator import MnistDiscriminator
    from models.CelebaDiscriminator import CelebaDiscriminator

    available_discriminators: Dict[str, torch.nn.Module] = {
        "cifar": CifarDiscriminator,
        "mnist": MnistDiscriminator,
        "celeba": CelebaDiscriminator,
    }
    verify_imports(available_discriminators, args.model)
    discriminator: torch.nn.Module = available_discriminators[args.model]

    from models.CifarGenerator import CifarGenerator, cifar_z_dim
    from models.MnistGenerator import MnistGenerator, mnist_z_dim
    from models.CelebaGenerator import CelebaGenerator, celeba_z_dim

    available_generators: Dict[str, torch.nn.Module] = {
        "cifar": (CifarGenerator, cifar_z_dim),
        "mnist": (MnistGenerator, mnist_z_dim),
        "celeba": (CelebaGenerator, celeba_z_dim),
    }
    verify_imports(available_generators, args.model)
    generator: torch.nn.Module = available_generators[args.model][0]
    z_dim = available_generators[args.model][1]

    from actors.server import server
    from actors.worker import worker

    np.random.seed(args.rank)
    random.seed(args.rank)
    torch.manual_seed(args.rank)
    torch.mps.manual_seed(args.rank)
    torch.cuda.manual_seed(args.rank)
    torch.cuda.manual_seed_all(args.rank)
    torch.backends.cudnn.deterministic = True

    # If the rank is greater than 0, we are a worker
    if args.rank > 0:
        # Initialize dataset with world size-1 because the server should not count as a worker
        discriminator = discriminator().to(device=args.device, dtype=torch.float32)
        discriminator.apply(weights_init)

        worker(
            backend=args.backend,
            rank=args.rank,
            world_size=args.world_size,
            batch_size=args.batch_size,
            swap_interval=args.swap_interval,
            data_partitioner=dataset,
            epochs=args.epochs,
            discriminator=discriminator,
            device=args.device,
            local_epochs=args.local_epochs,
            image_shape=image_shape,
            log_interval=args.log_interval,
            generator=None,
            discriminator_lr=args.discriminator_lr,
            generator_lr=args.generator_lr,
            z_dim=z_dim,
            log_folder=log_folder,
        )
    else:
        # If the rank is 0, we are the server
        generator = generator().to(device=args.device, dtype=torch.float32)
        generator.apply(weights_init)

        server(
            backend=args.backend,
            i=args.rank,
            world_size=args.world_size,
            batch_size=args.batch_size,
            epochs=args.epochs,
            generator=generator,
            dataset=dataset.train_dataset,
            device=args.device,
            image_shape=image_shape,
            generator_lr=args.generator_lr,
            z_dim=z_dim,
            log_interval=args.log_interval,
            log_folder=log_folder,
            iid=args.iid==1
        )

#!/bin/bash
. ./shared-args.sh
cd src

seed=3
world_size=13
backend=gloo
swap_interval=100
master_addr=localhost
master_port=1234
# network_interface=

python bootstrap.py \
    --backend $backend \
    --world_size $world_size \
    --dataset $dataset \
    --ranks $1 \
    --epochs $epochs \
    --local_epochs $local_epochs \
    --swap_interval $swap_interval \
    --discriminator_lr $discriminator_lr \
    --n_samples_fid $n_samples_fid \
    --generator_lr $generator_lr \
    --model $model \
    --device $device \
    --batch_size $batch_size \
    --iid $iid \
    --seed $seed \
    --master_addr $master_addr \
    --master_port $master_port \
    --log_interval $log_interval &

# Enable CTRL+C to stop all background processes
trap "trap - SIGTERM && kill -- -$$" SIGINT SIGTERM
# Wait for all background processes to complete
wait

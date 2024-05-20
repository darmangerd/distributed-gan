#!/bin/bash

batch_size=40
discriminator_lr=0.0002
generator_lr=0.0002
dataset=CIFAR10
model=$dataset
epochs=10000
local_epochs=1
iid=1
n_samples_fid=10
device=mps
log_interval=50
beta_1=0.0
beta_2=0.999

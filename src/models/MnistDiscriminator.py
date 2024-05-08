import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple
from dataloaders.MnistPartitioner import mnist_shape

ndf = 64

class MnistDiscriminator(nn.Module):
    def __init__(self, image_shape: Tuple[int, int, int] = mnist_shape):
        super(MnistDiscriminator, self).__init__()
        super().__init__()
        in_features = image_shape[0] * image_shape[1] * image_shape[2]

        # Discriminator will down-sample the input producing a binary output
        self.fc1 = nn.Linear(in_features=in_features, out_features=128)
        self.leaky_relu1 = nn.LeakyReLU(negative_slope=0.2)
        self.fc2 = nn.Linear(in_features=128, out_features=64)
        self.leaky_relu2 = nn.LeakyReLU(negative_slope=0.2)        
        self.fc3 = nn.Linear(in_features=64, out_features=32)
        self.leaky_relu3 = nn.LeakyReLU(negative_slope=0.2)        
        self.fc4 = nn.Linear(in_features=32, out_features=1)
        self.dropout = nn.Dropout(0.3)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Rehape passed image batch
        batch_size = x.shape[0]
        x = x.view(batch_size, -1)

        # Feed forward
        x = self.fc1(x)
        x = self.leaky_relu1(x)
        x = self.dropout(x)
        x = self.fc2(x)
        x = self.leaky_relu2(x)
        x = self.dropout(x)                        
        x = self.fc3(x)
        x = self.leaky_relu3(x)        
        x = self.dropout(x)
        logit_out = self.fc4(x)
        
        return logit_out.flatten()
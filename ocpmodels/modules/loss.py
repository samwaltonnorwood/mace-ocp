import logging

import torch
from torch import nn

from ocpmodels.common import distutils


class OCPLoss(nn.Module):
    def __init__(self, reduction="mean"):
        super().__init__()
        self.reduction = reduction
        assert reduction in ["mean", "sum"]

    def forward(self, input: torch.Tensor, target: torch.Tensor):
        raise NotImplementedError


# Following `weighted_mean_squared_error_energy` from MACE
# https://github.com/ACEsuit/mace/blob/main/mace/modules/loss.py#L18
class PerAtomMSELossEnergy(OCPLoss):
    def forward(
        self,
        input: torch.Tensor,
        target: torch.Tensor,
        natoms: torch.Tensor,
    ):
        dists = torch.square((target - input) / natoms)  # (nGraphs, )
        if self.reduction == "mean":
            return torch.mean(dists)
        elif self.reduction == "sum":
            return torch.sum(dists)


# Following `mean_squared_error_forces` from MACE
# https://github.com/ACEsuit/mace/blob/main/mace/modules/loss.py#L54
class MSELossForces(OCPLoss):
    def forward(self, input: torch.Tensor, target: torch.Tensor):
        dists = torch.square(target - input)  # (nAtoms, 3)
        dists = torch.mean(dists, dim=-1)  # (nAtoms, )
        if self.reduction == "mean":
            return torch.mean(dists)
        elif self.reduction == "sum":
            return torch.sum(dists)


class L2MAELoss(OCPLoss):
    def forward(self, input: torch.Tensor, target: torch.Tensor):
        dists = torch.norm(input - target, p=2, dim=-1)
        if self.reduction == "mean":
            return torch.mean(dists)
        elif self.reduction == "sum":
            return torch.sum(dists)


class AtomwiseL2Loss(OCPLoss):
    def forward(
        self,
        input: torch.Tensor,
        target: torch.Tensor,
        natoms: torch.Tensor,
    ):
        assert natoms.shape[0] == input.shape[0] == target.shape[0]
        assert len(natoms.shape) == 1  # (nAtoms, )

        dists = torch.norm(input - target, p=2, dim=-1)
        loss = natoms * dists

        if self.reduction == "mean":
            return torch.mean(loss)
        elif self.reduction == "sum":
            return torch.sum(loss)


class DDPLoss(nn.Module):
    def __init__(self, loss_fn, reduction="mean"):
        super().__init__()
        self.loss_fn = loss_fn
        self.loss_fn.reduction = "sum"
        self.reduction = reduction
        assert reduction in ["mean", "sum"]

    def forward(
        self,
        input: torch.Tensor,
        target: torch.Tensor,
        natoms: torch.Tensor = None,
        batch_size: int = None,
    ):
        # zero out nans, if any
        found_nans_or_infs = not torch.all(input.isfinite())
        if found_nans_or_infs is True:
            logging.warning("Found nans while computing loss")
            input = torch.nan_to_num(input, nan=0.0)

        if natoms is None:
            loss = self.loss_fn(input, target)
        else:  # atom-wise loss
            loss = self.loss_fn(input, target, natoms)
        if self.reduction == "mean":
            num_samples = (
                batch_size if batch_size is not None else input.shape[0]
            )
            num_samples = distutils.all_reduce(
                num_samples, device=input.device
            )
            # Multiply by world size since gradients are averaged
            # across DDP replicas
            return loss * distutils.get_world_size() / num_samples
        else:
            return loss

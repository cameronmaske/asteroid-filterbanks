from torch import nn
import torch
from . import transforms
from typing import Union


class ExponentialMovingAverage(nn.Module):
    """
    Computes the exponential moving average of an sequential input.

    Heavily inspired by leaf-audio's tensorflow implementation
        https://github.com/google-research/leaf-audio/blob/7ead2f9fe65da14c693c566fe8259ccaaf14129d/leaf_audio/postprocessing.py#L27

    See license here
        https://github.com/google-research/leaf-audio/blob/master/LICENSE
    """

    def __init__(
        self,
        smooth: float = 0.04,
        per_channel: bool = False,
        n_channels: int = 2,
        trainable: bool = False,
    ):
        super().__init__()
        if per_channel:
            self.weights = nn.Parameter(
                torch.full((n_channels,), fill_value=smooth), requires_grad=trainable
            )
        else:
            self.weights = nn.Parameter(
                torch.full((1,), fill_value=smooth), requires_grad=trainable
            )

    def forward(self, mag_spec, initial_state):
        weights = torch.clamp(self.weights, 0.0, 1.0)
        accumulator = initial_state
        out = None
        for x in torch.split(mag_spec, 1, dim=-1):
            accumulator = weights * x + (1.0 - weights) * accumulator
            if out is None:
                out = accumulator
            else:
                out = torch.cat((out, accumulator), dim=-1)
        return out


try:
    from typing import TypedDict
    class TrainableParameters(TypedDict):
        alpha: bool
        delta: bool
        root: bool
        smooth: bool
except ImportError:
    from typing import Dict
    TrainableParameters: Dict[str, bool]


class PCEN(nn.Module):
    """
        Per-Channel Energy Normalization as described in [1].

        PCEN is the use of an automatic gain control based dynamic compression to replace the widely used static compression.
        Optional, the parameters can be trained.

        Can
            This applies a fixed or learnable normalization by an exponential moving
    average smoother, and a compression.

        Args:
            alpha (float): exponent of EMA smoother
                Defaults to 0.96
            delta (float):
                Defaults to 2.0
            root (float):
                Defualts to 2.0
            floor (float):
                Defaults to 1e-6
            smooth (float):
                Defaults to 0.04
            n_channels (int):
                Defaults to 2
            trainable: (bool or )
                If fine-grain control is needed, you can control which individual parameters are
                trainable by passing a dictionary of booleans, with the key matching either
                "alpha", "delta", "root", "smooth"
                i.e. `{"alpha": False, "delta": True, "root": False, "smooth": True}`

                Defaults to False
            per_channel_smoothing: (bool):
                Defaults to False

        References
            [1]: Wang, Y., et al. "Trainable Frontend For Robust and Far-Field Keyword Spotting”, arXiv e-prints, 2016.
                 https://arxiv.org/pdf/1607.05666.pdf

        Heavily inspired by leaf-audio's tensorflow implementation
            https://github.com/google-research/leaf-audio/blob/7ead2f9fe65da14c693c566fe8259ccaaf14129d/leaf_audio/postprocessing.py

        See license here
            https://github.com/google-research/leaf-audio/blob/master/LICENSE
    """

    def __init__(
        self,
        alpha: float = 0.96,
        delta: float = 2.0,
        root: float = 2.0,
        floor: float = 1e-6,
        smooth: float = 0.04,
        n_channels: int = 2,
        trainable: Union[bool, TrainableParameters] = False,
        per_channel_smoothing: bool = False,
    ):
        super().__init__()

        if trainable is True or trainable is False:
            trainable = TrainableParameters(
                alpha=trainable, delta=trainable, root=trainable, smooth=trainable
            )

        self.floor = floor
        self.alpha = nn.Parameter(
            torch.full((n_channels,), fill_value=alpha), requires_grad=trainable["alpha"]
        )
        self.delta = nn.Parameter(
            torch.full((n_channels,), fill_value=delta), requires_grad=trainable["delta"]
        )
        self.root = nn.Parameter(
            torch.full((n_channels,), fill_value=root), requires_grad=trainable["root"]
        )

        self.ema = ExponentialMovingAverage(
            smooth=smooth,
            per_channel=per_channel_smoothing,
            n_channels=n_channels,
            trainable=trainable["smooth"],
        )

    def forward(self, tf_rep: torch.Tensor):
        """Computes the PCEN from a complex frequency representation.

        Args:
            tf_rep: (:class:`torch.Tensor`): A complex time frequency representation to compute the

        Shapes
            >>> (batch, n_channels, freq, time) -> (batch, n_channels, freq // 2 + 1, time)
        """
        if not transforms.is_asteroid_complex(tf_rep):
            raise AssertionError(
                f"Expected a tensor of shape (batch, n_channels, freq, time) but instead for {tf_rep.shape}."
            )
        mag_spec = transforms.mag(tf_rep, dim=-2)

        if len(tf_rep.shape) == 3:
            # If n_channels is 1, add a single dimension to keep the shape consistent with multi-channels
            mag_spec = mag_spec.unsqueeze(1)

        alpha = torch.min(self.alpha, torch.tensor(1.0))
        root = torch.max(self.root, torch.tensor(1.0))
        one_over_root = 1.0 / root
        initial_state = mag_spec[:, :, :, 0].unsqueeze(-1)
        ema_smoother = self.ema(mag_spec, initial_state=initial_state)
        # Equation (1) in [1]
        out = (
            mag_spec.transpose(1, -1) / (self.floor + ema_smoother.transpose(1, -1)) ** alpha
            + self.delta
        ) ** one_over_root - self.delta ** one_over_root
        return out.transpose(1, -1)

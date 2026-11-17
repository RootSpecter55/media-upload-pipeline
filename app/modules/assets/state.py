from app.core.errors import ConflictError
from app.modules.assets.models import Asset, AssetStatus

S = AssetStatus

# State machine: an asset has a real lifecycle, so every allowed move is listed here and any
# other move is refused. processing -> uploaded is the retry path after an unexpected error.
ALLOWED_TRANSITIONS: dict[AssetStatus, frozenset[AssetStatus]] = {
    S.PENDING_UPLOAD: frozenset({S.UPLOADED}),
    S.UPLOADED: frozenset({S.PROCESSING}),
    S.PROCESSING: frozenset({S.READY, S.REJECTED, S.UPLOADED}),
    S.READY: frozenset(),
    S.REJECTED: frozenset(),
}


class InvalidTransitionError(ConflictError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Asset cannot move from {current} to {target}")


def can_transition(current: str, target: AssetStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[AssetStatus(current)]


def transition(asset: Asset, target: AssetStatus) -> None:
    """Moves the asset to `target`, or raises if the table does not allow it."""
    if not can_transition(asset.status, target):
        raise InvalidTransitionError(asset.status, target)
    asset.status = target

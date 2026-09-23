"""Per-conversation changeset persistence + staging operations.

One changeset per chat, stored as the ordered ``Conversation.changeset`` JSON.
This is the only durable thing the agent authors: the staging env is always
derived by replaying it. The store validates every appended action against the
Action schema (so a malformed action never lands) and drives the resolver to
materialize / test / apply.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from datapro_ai.models import Conversation
from datapro_ai.staging.actions import Action, Changeset
from datapro_ai.staging.core_client import CoreClient
from datapro_ai.staging.resolver import Resolver
from pydantic import TypeAdapter, ValidationError

_ACTION = TypeAdapter(Action)


class ChangesetError(Exception):
    """A bad action or a changeset operation that can't be applied."""


class ChangesetStore:
    def __init__(self, session: Session, conversation: Conversation):
        self.session = session
        self.conversation = conversation

    # -- read --------------------------------------------------------------

    def raw(self) -> list[dict]:
        return list(self.conversation.changeset or [])

    def changeset(self) -> Changeset:
        """Parsed + validated changeset. Raises ChangesetError if a stored
        action is malformed (shouldn't happen — append validates)."""
        try:
            return Changeset.model_validate({"actions": self.raw()})
        except ValidationError as exc:
            raise ChangesetError(f"stored changeset is invalid: {exc}") from exc

    # -- write -------------------------------------------------------------

    def append(self, action: dict) -> dict:
        """Validate a single action dict and append it. Returns the stored
        action (with its minted id). Persisted immediately."""
        try:
            parsed = _ACTION.validate_python(action)
        except ValidationError as exc:
            raise ChangesetError(f"invalid action: {exc}") from exc
        stored = parsed.model_dump(mode="json")
        actions = self.raw()
        actions.append(stored)
        self._save(actions)
        return stored

    def remove(self, action_id: str) -> None:
        actions = [a for a in self.raw() if a.get("id") != action_id]
        if len(actions) == len(self.raw()):
            raise ChangesetError(f"no action with id {action_id!r}")
        self._save(actions)

    def reorder(self, action_ids: list[str]) -> None:
        """Replace the order with ``action_ids`` — must be a permutation of the
        current ids (nothing added or dropped)."""
        current = {a["id"]: a for a in self.raw()}
        if set(action_ids) != set(current):
            raise ChangesetError("reorder must be a permutation of existing action ids")
        self._save([current[i] for i in action_ids])

    def clear(self) -> None:
        self._save([])

    def _save(self, actions: list[dict]) -> None:
        # Reassign (not mutate) so SQLAlchemy notices the JSON column changed.
        self.conversation.changeset = actions
        self.session.add(self.conversation)
        self.session.commit()

    # -- staging operations (via the resolver) ----------------------------

    def materialize(self, core: CoreClient) -> str:
        """Build a fresh staging env from the current changeset. Returns the env
        id. The caller queries it, then drops it when done."""
        return Resolver(core).materialize(
            self.changeset(), label=f"chat:{self.conversation.id}"
        )

    def apply(self, core: CoreClient) -> dict:
        """Rebuild off current prod + promote atomically. Returns the promote
        result (or raises via the resolver on a conflict)."""
        return Resolver(core).apply(
            self.changeset(), label=f"chat:{self.conversation.id}"
        )

    # -- saved tests -------------------------------------------------------

    def tests(self) -> list[dict]:
        return list(self.conversation.stage_tests or [])

    def add_test(self, test: dict[str, Any]) -> None:
        tests = self.tests()
        tests.append(test)
        self.conversation.stage_tests = tests
        self.session.add(self.conversation)
        self.session.commit()

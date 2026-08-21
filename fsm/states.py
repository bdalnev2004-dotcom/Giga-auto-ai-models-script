from aiogram.fsm.state import State, StatesGroup


class AccountContext(StatesGroup):
    """Persistent-ish state: which account this chat is currently working in."""
    waiting_for_account_number = State()


class ScenarioDialog(StatesGroup):
    """
    Generic state machine for ANY scenario (top-level or step).
    The clarifying-question list is scenario-specific (see handlers/scenarios.py's
    QUESTION_BANK) but the flow itself is identical for all of them:
    ask -> collect -> ask next -> ... -> generate -> approval.
    """
    collecting_answers = State()
    awaiting_generation = State()
    awaiting_approval = State()
    awaiting_revision_notes = State()  # if the person rejects a batch (❌)


class ApprovalQueue(StatesGroup):
    """State for when a batch of numbered variants is sitting in Drive awaiting a reply."""
    awaiting_numbers_or_emoji = State()

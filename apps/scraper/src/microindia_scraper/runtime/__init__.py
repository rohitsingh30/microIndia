"""Reusable browser-task runtime shared by the sourcer and the scraper.

One signed-in Chrome is attached over CDP. Runner processes lease tasks of the
kinds they were started with, borrow a tab, call the registered handler and
apply its result. Handlers return follow-up tasks; that is how sourcing feeds
scraping and scraping feeds sourcing.
"""

from .registry import TaskContext, handler, handlers_for, registered_kinds
from .results import AuthBlocked, Done, Fail, FollowUp, Retry, Skip
from .tasks import TaskStore

__all__ = [
    "AuthBlocked",
    "Done",
    "Fail",
    "FollowUp",
    "Retry",
    "Skip",
    "TaskContext",
    "TaskStore",
    "handler",
    "handlers_for",
    "registered_kinds",
]

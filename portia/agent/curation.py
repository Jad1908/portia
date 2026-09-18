"""Whether the copilot may end a reply without keeping what it learned.

`docs/FINDINGS.md` §5.3. `review_queries` and `record_finding` were called
**zero times** across every chat in the sandbox — 27 `query_data` and
`plot_data` calls, two prompt files and a section of `copilot.md` saying *call
this before every reply* in capitals, and no journal anywhere. §2 already
recorded the shape of that failure once: the strongest possible prose was
sitting in the file that needed it, and the model still told the user its tools
could not do the thing. A third paragraph is not the fix.

So the rule moves out of prose and into the loop. The SDK fires a ``Stop`` hook
when the model is about to end its turn, and a hook may block the stop once
with a reason the model reads. This module is the whole of that decision, kept
pure so it can be tested without a model: it watches which tools ran this
exchange, and it holds the reply **once** if the data was asked something and
nothing reviewed it afterwards.

Once, and never twice, is what keeps it from becoming a loop that never ends:
the model may decide nothing it asked was worth keeping, and `review_queries`
is the act of deciding. Calling it clears the hold whether or not a finding
follows. A reply blocked a second time on the same exchange would be the
harness arguing with a judgment the prose already says is the agent's.
"""

from __future__ import annotations

from portia import findings
from portia.agent import events

#: The tool whose call is the act of reviewing. Naming it here rather than in
#: `findings` because this is the only place it matters *as a tool call*.
REVIEW_TOOL = "review_queries"


class Curation:
    """The hold, as state: did this exchange ask the data, and was that reviewed?"""

    def __init__(self) -> None:
        self.asked = False
        self.held = False

    def start_exchange(self) -> None:
        """A new message from the human. Nothing has been asked yet."""
        self.asked = False
        self.held = False

    def saw_tool(self, name: str) -> None:
        """A tool ran. A question marks the exchange; a review clears it."""
        label = events.tool_label(name)
        if label in findings.QUERY_TOOLS:
            self.asked = True
        elif label == REVIEW_TOOL:
            self.asked = False

    def hold(self) -> bool:
        """Whether to block this stop. True at most once per exchange."""
        if self.asked and not self.held:
            self.held = True
            return True
        return False

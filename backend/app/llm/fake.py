"""Scripted offline LLM (LLM_MODE=fake) for tests, CI and `run.py --demo`: follows the same tool-calling
and text contracts as the real model, so the whole pipeline runs without a key or network."""
from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage, ToolMessage

from .gateway import Provider

ORDER = ["analyse_bank_statement", "pull_credit_report", "check_affordability", "score_credit_risk",
         "run_policy_checks"]


class FakeProvider(Provider):
    delay = 0.0

    def model_name(self, tier):
        return f"fake-{tier}"

    def tools(self, tier, kind, messages, tools):
        done = set()
        ids = {}
        for m in messages:
            if isinstance(m, AIMessage):
                for c in m.tool_calls or []:
                    ids[c["id"]] = c["name"]
            if isinstance(m, ToolMessage) and not str(m.content).startswith(("REFUSED", "ERROR")):
                done.add(ids.get(m.tool_call_id))
        missing = [n for n in ORDER if n not in done]
        if not missing:
            return AIMessage(content="All checks are complete."), 0
        nxt = missing[:2] if missing[0] in ORDER[:2] else missing[:1]          # statement + bureau together
        return AIMessage(content=f"Next I need: {', '.join(nxt)}.", tool_calls=[
            {"name": n, "args": {}, "id": f"call_{n}"} for n in nxt]), 0

    def text(self, tier, kind, messages, on_token, max_tokens):
        body = str(messages[-1].content)
        if kind == "memo":
            f = json.loads(body)
            r = f["system_recommendation"]
            out = (f"## Summary\nVerified income ₹{f['affordability']['verified_income']:,.0f}, FOIR "
                   f"{f['affordability']['foir']:.0%}, risk band {f['risk']['band']}.\n\n## Strengths\n- Statement and "
                   f"bureau data available\n\n## Concerns\n" + ("\n".join(f"- {x}" for x in f['policy_fails']['hard']
                                                                   + f['policy_fails']['soft']) or "- None")
                   + f"\n\n## Recommendation\n**{r['decision'].upper()}** - {r['summary']}")
        elif kind == "letter":
            m = re.search(r'"decision": "(\w+)"', body)
            out = f"Dear Applicant,\n\nThis letter confirms the outcome of your loan application: " \
                  f"**{(m.group(1) if m else 'decided').upper()}**.\n\nRegards,\nLendWise Credit Team"
        elif kind == "case_qa":
            out = "Based on the assessment data: the main factors are listed in the risk and policy sections."
        else:
            out = "OK."
        if on_token:
            on_token(out)
        return out, 0

    def structured(self, tier, kind, messages, schema):
        raise NotImplementedError("LendWise does not use structured calls")

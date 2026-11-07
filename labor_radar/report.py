"""Turn an Assessment into what the worker reads: localized, with citations and arithmetic."""

from labor_radar import fmt, paperwork, referrals
from labor_radar.engine import Assessment
from labor_radar.i18n import is_draft, resolve, t
from labor_radar.model import Employment
from labor_radar.rules.core import (
    Applicability,
    Calc,
    Finding,
    Gate,
    Rule,
    Verdict,
    reason_key,
)

GENERAL_EVIDENCE = ("contract", "messages", "keep_copy")


def _calc(lang: str, calc: Calc) -> dict:
    return {"text": t(lang, calc.template, **calc.params), "amount": fmt.money(calc.amount)}


def _item(lang: str, employment: Employment, rule: Rule, finding: Finding) -> dict:
    return {
        "rule_id": rule.id,
        "title": t(lang, f"rule.{rule.id}.title"),
        "verdict": finding.verdict.value,
        "explanation": t(lang, f"rule.{rule.id}.{finding.variant}", **finding.params),
        "citations": [{"label": c.label, "url": c.url} for c in rule.citations],
        "dates": [fmt.day(d) for d in finding.dates],
        "calcs": [_calc(lang, c) for c in finding.calcs],
        "amount": fmt.money(finding.amount) if finding.calcs else None,
        "why_human": [t(lang, reason_key(g, employment)) for g in finding.uncertain_gates],
    }


def _claim(lang: str, assessment: Assessment) -> dict:
    claim = assessment.claim
    if claim.required is None:
        return {"complete": False, "message": t(lang, "claim.incomplete")}
    slips = assessment.record.payslips
    paid_label = (
        "claim.paid_assumed" if claim.paid_assumed
        else "claim.paid_self" if all(s.self_reported for s in slips)
        else "claim.paid_payslip"
    )
    lines = [_calc(lang, claim.base)] + [_calc(lang, c) for f in claim.components for c in f.calcs]
    shortfall = claim.shortfall
    return {
        "complete": True,
        "lines": lines,
        "required": fmt.money(claim.required),
        "paid_label": t(lang, paid_label),
        "paid": fmt.money(claim.paid),
        "shortfall": str(fmt.round_ntd(shortfall)),
        "settled": shortfall == 0,
        "settled_message": t(lang, "claim.settled") if shortfall == 0 else None,
        "possible_extra": fmt.money(claim.possible_extra) if claim.possible else None,
        "possible_label": t(lang, "claim.possible"),
        "rate_note": t(lang, "claim.rate_note"),
    }


def _evidence(lang: str, assessment: Assessment) -> list[str]:
    keys: list[str] = []
    for result in assessment.results:
        if any(f.verdict is not Verdict.NOTE for f in result.findings):
            keys += [k for k in result.rule.evidence if k not in keys]
    keys += [k for k in GENERAL_EVIDENCE if k not in keys]
    return [t(lang, f"evidence.{k}") for k in keys]


def _notices(lang: str, assessment: Assessment) -> list[str]:
    seen: list[str] = []
    for result in assessment.results:
        for gate, status in result.gates.items():
            key = None
            if status is Applicability.NOT_APPLICABLE:
                key = f"gate.{gate.value}" if gate is Gate.DOMESTIC_WORKER else f"gate.{gate.value}.not_applicable"
            if key and key not in seen:
                seen.append(key)
    return [t(lang, k) for k in seen]


def render(assessment: Assessment, lang: str = "zh-TW") -> dict:
    lang = resolve(lang)
    employment = assessment.record.employment
    groups = {v: [_item(lang, employment, r, f) for r, f in assessment.findings(v)] for v in Verdict}
    claim = _claim(lang, assessment)
    record = assessment.record
    summary = [t(lang, "summary.counts", violations=len(groups[Verdict.VIOLATION]),
                 needs_human=len(groups[Verdict.NEEDS_HUMAN]))]
    if claim["complete"] and not claim["settled"]:
        summary.append(t(lang, "summary.shortfall", amount=f"{int(claim['shortfall']):,}"))
    return {
        "lang": lang,
        "draft_translation": is_draft(lang),
        "draft_banner": t(lang, "ui.draft_banner") if is_draft(lang) else None,
        "disclaimer": t(lang, "disclaimer.long"),
        "summary": summary,
        "worker": record.worker.name,
        "employer": record.employer.name,
        "period": {"start": fmt.day(record.period.start), "end": fmt.day(record.period.end)},
        "pay_basis": record.employment.pay_basis.value,
        "violations": groups[Verdict.VIOLATION],
        "needs_human": groups[Verdict.NEEDS_HUMAN],
        "notes": groups[Verdict.NOTE],
        "not_checked": _notices(lang, assessment),
        "claim": claim,
        "evidence": _evidence(lang, assessment),
        "mediation_draft": paperwork.mediation_draft(assessment),
        "referrals": referrals.for_city(lang, record.city),
    }


def to_markdown(view: dict) -> str:
    out = [f"# {t(view['lang'], 'section.summary')}：{view['worker']} / {view['employer']}",
           f"期間 {view['period']['start']} ~ {view['period']['end']}", ""]
    out += [f"- {line}" for line in view["summary"]] + [""]
    for key, section in (("violations", "section.violations"), ("needs_human", "section.needs_human"),
                         ("notes", "section.notes")):
        if not view[key]:
            continue
        out.append(f"## {t(view['lang'], section)}")
        for item in view[key]:
            out.append(f"### {item['title']}（{'、'.join(c['label'] for c in item['citations'])}）")
            out.append(item["explanation"])
            out += [f"- 為什麼要專人判斷：{w}" for w in item["why_human"]]
            out += [f"- {c['text']} = {c['amount']} 元" for c in item["calcs"]]
            out += [f"- 法條連結：{c['url']}" for c in item["citations"]]
            out.append("")
    claim = view["claim"]
    out.append(f"## {t(view['lang'], 'section.claim')}")
    if claim["complete"]:
        out += [f"- {line['text']} = {line['amount']} 元" for line in claim["lines"]]
        out.append(f"- {t(view['lang'], 'claim.required')}：{claim['required']} 元")
        out.append(f"- {claim['paid_label']}：{claim['paid']} 元")
        out.append(f"- **{t(view['lang'], 'claim.shortfall')}：{int(claim['shortfall']):,} 元**")
        if claim["possible_extra"]:
            out.append(f"- {claim['possible_label']}：{claim['possible_extra']} 元")
        if claim["settled_message"]:
            out.append(f"- {claim['settled_message']}")
        out.append(f"- {claim['rate_note']}")
    else:
        out.append(claim["message"])
    out += ["", f"## {t(view['lang'], 'section.evidence')}"] + [f"- [ ] {e}" for e in view["evidence"]]
    if view["not_checked"]:
        out += ["", f"## {t(view['lang'], 'section.not_checked')}"] + [f"- {n}" for n in view["not_checked"]]
    out += ["", f"## {t(view['lang'], 'section.mediation')}", "```text", view["mediation_draft"], "```"]
    out += ["", f"## {t(view['lang'], 'section.referrals')}"]
    out += [f"- {r['name']}：{r['detail']}（{r['url']}）" for r in view["referrals"]]
    out += ["", f"> {view['disclaimer']}"]
    return "\n".join(out)


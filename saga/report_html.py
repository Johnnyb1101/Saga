"""Render verified report evidence as a standalone, readable HTML document."""

from html import escape


def _html(value):
    return escape(str(value), quote=True)


def _display(value):
    return "—" if value is None else str(value)


def _require_keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid fields")


def validate_narrative(narrative, evidence):
    """Accept only bounded prose with references to facts in this exact report."""
    _require_keys(narrative, ("schema_version", "report_id", "sections"), "narrative")
    if narrative["schema_version"] != 1 or type(narrative["schema_version"]) is not int:
        raise ValueError("unsupported narrative schema")
    if narrative["report_id"] != evidence.get("report_id") or not isinstance(narrative["report_id"], str):
        raise ValueError("narrative report ID does not match evidence")
    sections = narrative["sections"]
    if not isinstance(sections, list) or len(sections) > 20:
        raise ValueError("narrative has too many sections")
    facts = evidence.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("report facts are missing")  # noqa: TRY004 - narrative API uses ValueError
    total_characters = 0
    total_segments = 0
    for section in sections:
        _require_keys(section, ("heading", "paragraphs"), "section")
        heading = section["heading"]
        if not isinstance(heading, str) or not heading.strip() or len(heading) > 200 or any(ch.isdigit() for ch in heading):
            raise ValueError("heading must be short text without literal digits")
        total_characters += len(heading)
        paragraphs = section["paragraphs"]
        if not isinstance(paragraphs, list) or not paragraphs or len(paragraphs) > 20:
            raise ValueError("section needs at most 20 paragraphs")
        for paragraph in paragraphs:
            if not isinstance(paragraph, list) or not paragraph or len(paragraph) > 50:
                raise ValueError("paragraph needs at most 50 segments")
            total_segments += len(paragraph)
            for segment in paragraph:
                if not isinstance(segment, dict) or len(segment) != 1:
                    raise ValueError("segment must contain one text or fact field")
                if "text" in segment:
                    value = segment["text"]
                    if not isinstance(value, str) or not value or len(value) > 2000 or any(ch.isdigit() for ch in value):
                        raise ValueError("narrative text must omit literal digits")
                    total_characters += len(value)
                elif "fact" in segment:
                    fact_id = segment["fact"]
                    if not isinstance(fact_id, str) or fact_id not in facts:
                        raise ValueError("narrative references an unknown fact")
                else:
                    raise ValueError("unknown narrative segment")
    if total_characters > 100_000 or total_segments > 2_000:
        raise ValueError("narrative is too large")
    return narrative


def _fact_link(fact_id, evidence):
    if fact_id not in evidence["facts"]:
        raise ValueError("report references an unknown fact")
    fact = evidence["facts"][fact_id]
    return f'<a href="#fact-{_html(fact_id)}">{_html(fact["display"])}</a>'


def _table(headers, rows):
    if not rows:
        return '<p class="empty">No entries in this scope.</p>'
    head = "".join(f"<th scope=\"col\">{_html(value)}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
    return f"<div class=\"table-wrap\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _measurement_table(rows, evidence):
    return _table(("Group", "Total", "Occasions"), [
        (_html(_display(row["label"])), _fact_link(row["fact_id"], evidence), _html(row["occasions"]))
        for row in rows
    ])


def _record_anchor(record_id):
    return f"record-{_html(record_id)}"


def render_html(evidence, narrative=None):
    """Return escaped, self-contained HTML from verified evidence.

    Narrative prose is a review draft. Fact references are checked, but prose
    meaning cannot be established by this syntactic validator.
    """
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("unsupported evidence schema")
    verification = evidence.get("verification")
    if not isinstance(verification, dict) or verification.get("passed") is not True:
        raise ValueError("report evidence is not verified")
    if not isinstance(evidence.get("facts"), dict):
        raise ValueError("report facts are missing")  # noqa: TRY004 - report API uses ValueError
    if narrative is not None:
        validate_narrative(narrative, evidence)

    scope = evidence["scope"]
    summary = evidence["summary"]
    measurements = evidence["measurements"]
    records = evidence["records"]
    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f"<title>Accomplishment report {_html(evidence['report_id'])}</title>",
             """<style>
*{box-sizing:border-box}body{margin:0;background:#f5f4f0;color:#242a2e;font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1080px;margin:auto;padding:2rem 1.2rem 5rem}h1,h2,h3{line-height:1.2;color:#163045}h1{font-size:2.4rem}h2{margin-top:2.5rem;border-bottom:2px solid #bac8ce;padding-bottom:.35rem}h3{margin-top:1.8rem}p,li{max-width:75ch}a{color:#075c83}a:hover{text-decoration-thickness:2px}.lede,.note{background:#e9f0f1;border-left:4px solid #4e7b89;padding:.8rem 1rem}.draft{background:#fff8e8;border:1px solid #d6b774;padding:1rem 1.3rem}.cards{display:flex;flex-wrap:wrap;gap:1rem}.card{background:#fff;border:1px solid #d9e0e2;border-radius:.4rem;padding:.8rem 1rem;min-width:13rem}.card strong{display:block;font-size:1.5rem}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:#fff;margin:.6rem 0 1.2rem}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #d9e0e2;padding:.55rem .7rem}th{background:#e9f0f1}details{background:#fff;border:1px solid #d9e0e2;border-radius:.3rem;padding:.6rem .8rem;margin:.5rem 0}summary{cursor:pointer;font-weight:600}.facts{columns:2;column-gap:2rem}.facts li{break-inside:avoid;margin-bottom:.65rem}.small{color:#52616b;font-size:.9rem}.empty{color:#52616b}@media(max-width:650px){.facts{columns:1}h1{font-size:1.9rem}}
</style></head><body><main>""",
             "<h1>Accomplishment report</h1>",
             f"<p class=\"small\">Report ID: <code>{_html(evidence['report_id'])}</code></p>",
             '<p class="lede">Counts and totals below come from verified completion records. Each measure keeps its own unit. Unknown measurements are not counted as zero. Corrections use the latest completion revision.</p>',
             "<h2>Scope</h2><dl>"]
    labels = (("since", "From (inclusive)"), ("until", "Through (inclusive)"),
              ("category", "Category"), ("duty", "Role"))
    for key, label in labels:
        value = scope.get(key)
        parts.append(f"<dt>{_html(label)}</dt><dd>{_html(value) if value is not None else 'All'}</dd>")
    parts.append("</dl><h2>Summary</h2><div class=\"cards\">")
    for key, value in summary.items():
        label = key.replace("_", " ").capitalize()
        fact_id = next((fid for fid, fact in evidence["facts"].items()
                        if fact.get("dimension") == "summary" and fact.get("group") == key), None)
        rendered = _fact_link(fact_id, evidence) if fact_id is not None else _html(value)
        parts.append(f'<div class="card"><span>{_html(label)}</span><strong>{rendered}</strong></div>')
    parts.append("</div><h2>Measured results</h2>")
    if not measurements:
        parts.append('<p class="empty">No measured quantities in this scope.</p>')
    for measure in measurements:
        parts.append(f"<section><h3>{_html(measure['measure'])}</h3>")
        parts.append(f"<p>Total: <strong>{_fact_link(measure['fact_id'], evidence)}</strong> "
                     f"across {_html(measure['occasions'])} occasions.</p>")
        for key, heading in (("by_category", "By category"), ("by_role", "By role"), ("by_month", "By month")):
            parts.append(f"<h4>{heading}</h4>")
            parts.append(_measurement_table(measure[key], evidence))
        parts.append("</section>")

    if narrative is not None:
        parts.append('<section class="draft"><h2>Agent narrative — draft for review</h2>')
        parts.append('<p class="small">Fact references are validated against this report. The wording and interpretation still need human review.</p>')
        for section in narrative["sections"]:
            parts.append(f"<h3>{_html(section['heading'])}</h3>")
            for paragraph in section["paragraphs"]:
                parts.append("<p>")
                for segment in paragraph:
                    parts.append(_html(segment["text"]) if "text" in segment else _fact_link(segment["fact"], evidence))
                parts.append("</p>")
        parts.append("</section>")

    parts.append('<h2 id="facts">Fact references</h2><p class="small">These values are computed from the linked source records.</p><ol class="facts">')
    for fact_id, fact in evidence["facts"].items():
        source_links = ", ".join(f'<a href="#{_record_anchor(source_id)}">{_html(source_id)}</a>'
                                 for source_id in fact.get("source_record_ids", []))
        parts.append(f'<li id="fact-{_html(fact_id)}"><strong>{_html(fact["label"])}</strong>: '
                     f'{_html(fact["display"])}<br><span class="small">Source records: {source_links or "none"}</span></li>')
    parts.append("</ol><h2>Completion records</h2>")
    for record in records:
        record_id = record["id"]
        title = record.get("outcome") or record.get("task_title") or "Completion"
        parts.append(f'<details id="{_record_anchor(record_id)}"><summary>#{_html(record_id)} · '
                     f'{_html(record.get("completed_at", ""))} · {_html(title)}</summary>')
        parts.append(_table(("Field", "Saved value"), [(_html(key.replace("_", " ")), _html(_display(value)))
                                                   for key, value in record.items()]))
        parts.append("</details>")
    parts.append("</main></body></html>")
    return "".join(parts)

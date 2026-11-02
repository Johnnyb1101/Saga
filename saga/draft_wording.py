"""Conservative cleanup of wording beside measured facts in new AI drafts.

The caller validates the original and returned narrative. This function never
edits evidence, preparation, headings, fact references, or saved source prose.
"""

from copy import deepcopy

_END_PUNCTUATION = ".,;:!?"


def _unquoted_before(paragraph, fact_index):
    prior = "".join(segment["text"] for segment in paragraph[:fact_index]
                    if "text" in segment)
    return not any(marker in prior for marker in ('"', "'", "“", "”", "‘", "’"))


def _has_exact_prefix(segments, measure):
    text = "".join(segment["text"] for segment in segments)
    prefix = " of "
    if (len(text) < len(prefix) + len(measure)
            or text[0] != " " or text[1:3].casefold() != "of"
            or text[3] != " " or text[4:4 + len(measure)] != measure):
        return False
    remainder = text[4 + len(measure):]
    if not remainder:
        return True
    if remainder[0].isspace():
        remainder = remainder.lstrip()
    return (bool(remainder) and remainder[0] in _END_PUNCTUATION
            and (len(remainder) == 1 or remainder[1].isspace()))


def _without_of(segments):
    """Remove 'of ' across a text run without joining its segments."""
    result = []
    offset = 0
    for segment in segments:
        value = segment["text"]
        cleaned = "".join(char for index, char in enumerate(value, offset)
                          if index not in (1, 2, 3))
        if cleaned:
            result.append({"text": cleaned})
        offset += len(value)
    return result


def clean_draft_wording(narrative, evidence, preparation=None):
    """Copy a validated new draft, removing only surplus 'of' beside a fact.

    General measured facts render a number without a unit. Only their exact,
    case-sensitive measure immediately after " of " qualifies for correction.
    """
    result = deepcopy(narrative)
    if preparation is not None and preparation["report_format"] != "general":
        return result

    for section in result["sections"]:
        for paragraph_index, paragraph in enumerate(section["paragraphs"]):
            revised = []
            index = 0
            while index < len(paragraph):
                segment = paragraph[index]
                revised.append(segment)
                index += 1
                if "fact" not in segment:
                    continue
                fact = evidence["facts"].get(segment["fact"], {})
                measure = fact.get("measure")
                start = index
                while index < len(paragraph) and "text" in paragraph[index]:
                    index += 1
                run = paragraph[start:index]
                if (isinstance(measure, str) and measure
                        and fact.get("dimension") not in {"summary", "selected_record"}
                        and run and _unquoted_before(paragraph, start - 1)
                        and _has_exact_prefix(run, measure)):
                    revised.extend(_without_of(run))
                else:
                    revised.extend(run)
            section["paragraphs"][paragraph_index] = revised
    return result

"""Safe, fixed explanations for AI drafting failures."""

ERROR_MESSAGES = {
    "authentication": "OpenAI rejected authentication. Check your API key and project access.",
    "permission": "OpenAI denied access. Check your project and regional access.",
    "model": "OpenAI could not use the requested model. Check model availability and access.",
    "quota": "OpenAI reports exhausted credits or an account or project usage or spending limit. Check API billing and limits.",
    "rate_limit": "OpenAI limited the request. This may be a rate or account usage limit.",
    "rejected": "OpenAI rejected the draft request. Check the request settings.",
    "service": "OpenAI's service could not complete the request. Try again later.",
    "redirect": "OpenAI redirected the request, so the draft was stopped.",
    "connection": "Could not connect to OpenAI. Check your network connection.",
    "dns": "Could not find OpenAI's server. Check your network or DNS settings.",
    "tls": "Could not establish a secure connection to OpenAI.",
    "timeout": "The OpenAI request timed out. Try again.",
    "too_large": "OpenAI's response was too large to process.",
    'draft_incomplete': 'OpenAI stopped before finishing the draft. Try a shorter request.',
    'refused': 'OpenAI declined to produce this draft. Review the selected content before trying again.',
    'response_shape': 'OpenAI returned an unsupported response format. Saga could not read the draft.',
    'invalid_json': 'OpenAI returned a draft that was not valid structured text.',
    'report_mismatch': 'The draft did not match the approved report and was rejected.',
    'selection_mismatch': 'The draft did not match the selected accomplishments and was rejected.',
    'literal_numbers': "The draft wrote numbers directly instead of using Saga's verified number references.",
    'unknown_fact': 'The draft referenced a figure outside the approved data and was rejected.',
    'missing_fact': 'The draft omitted a required verified figure and was rejected.',
    'unresolved_placeholder': 'The draft left a figure placeholder unresolved and was rejected.',
    'unverified_number': 'The draft wrote an unverified quantity and was rejected.',
    'draft_structure': "The draft did not meet Saga's required layout or text rules.",
    'draft_size': 'Too much data for one draft. Narrow the date range or filters, or select fewer accomplishments.',
    'invalid_evidence': 'Saga could not verify the report data. Reopen the report before trying again.',
    'invalid_preparation': 'The selected draft data no longer matches the report. Reopen it and select accomplishments again.',
    'invalid_model': 'The model name is invalid. Check the model name before sending.',
    "unknown": "The OpenAI request failed. Try again or check your settings.",
}


def safe_failure_message(reason: str) -> str:
    """Return fixed text, even for an untrusted or future failure reason."""
    if not isinstance(reason, str):
        return ERROR_MESSAGES["unknown"]
    return ERROR_MESSAGES.get(reason, ERROR_MESSAGES["unknown"])

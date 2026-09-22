"""Explicit terminal handoff; never polls or imports a browser session."""
from __future__ import annotations
from .errors import AppError, AccessError, ParseError
from .page_sources import HttpPageSource, LocalHtmlPageSource, parse_document
from .urls import redact_url


def _check_requested(page, parser, context):
    wanted = context.explicit_server or (
        parser.fragment_section(context.fragment_hint)
        if context.fragment_hint and hasattr(parser, 'fragment_section') else None)
    if wanted:
        section = next((s for s in page.sections if s.section_id == wanted), None)
        if section is None or not section.groups:
            raise ParseError('missing_section: selected server is absent/not loaded or contains no safe audio; no fallback',
                             'missing_section')
    if not page.sections and not page.groups:
        raise ParseError('no_audio: no executable audio context in the input', 'no_audio')
    return page


def obtain_page(*, context, parser, config, client, html_file, html_encoding,
                dry_run, interactive, ask, emit, browser_open):
    def local(path):
        document = LocalHtmlPageSource(path, config, parser.policy, html_encoding).load(context)
        return _check_requested(parse_document(document, parser, context), parser, context)

    if html_file is not None:
        return local(html_file)
    try:
        document = HttpPageSource(client, parser.policy).load(context)
        return _check_requested(parse_document(document, parser, context), parser, context)
    except AccessError as error:
        if error.decision.kind not in {'needs_human_verification', 'suspected_verification'}:
            raise
        emit(error.decision.diagnostic_message)
        emit(f'provider={error.decision.provider}; actual_attempts={error.attempts}; automatic requests stopped.')
        emit('\u7a0b\u5e8f\u4e0d\u4f1a\u81ea\u52a8\u89e3\u9a8c\u8bc1\u6216\u8bfb\u53d6\u6d4f\u89c8\u5668 Cookie\u3002')
        emit('Import example (use body HTML saved through normal authorized access):')
        emit(f'python main.py "{redact_url(context.input_url)}"'
             + (f' --server {context.explicit_server}' if context.explicit_server else '')
             + ' --html-file "quotes.html" --dry-run')
        if dry_run or not interactive:
            raise
    # The HTTP response is already closed. All subsequent attempts are local I/O.
    emit('[1] \u5bfc\u5165\u5df2\u4fdd\u5b58\u7684\u6b63\u6587 HTML')
    emit('[2] \u5728\u9ed8\u8ba4\u6d4f\u89c8\u5668\u6253\u5f00\u539f\u9875\u9762\uff0c\u7531\u6211\u81ea\u884c\u5904\u7406')
    emit('[0] \u9000\u51fa\uff0c\u4fdd\u7559\u5df2\u6709\u6570\u636e')
    while True:
        answer = ask('\u8bf7\u9009\u62e9\uff1a').strip()
        if answer == '0':
            return None
        if answer in {'1', '2'}:
            break
        emit('Please choose 1, 2 or 0.')
    if answer == '2':
        try:
            opened = browser_open(context.input_url)
        except Exception:
            opened = False
        emit(('\u5df2\u5c1d\u8bd5\u6253\u5f00\u539f\u9875\u9762\uff0c\u4e0d\u4ee3\u8868\u9a8c\u8bc1\u6210\u529f\u3002' if opened else
              '\u65e0\u6cd5\u6253\u5f00\u6d4f\u89c8\u5668\uff0c\u8bf7\u624b\u52a8\u8bbf\u95ee\uff1a') + redact_url(context.input_url))
        emit('Save the visible voice content as UTF-8 HTML/TXT; no Cookie, HAR or Console script is required.')
    while True:
        path = ask('\u8bf7\u8f93\u5165\u5df2\u4fdd\u5b58\u7684 HTML \u6587\u4ef6\u8def\u5f84\uff08\u8f93\u5165 0 \u53d6\u6d88\uff09\uff1a').strip()
        if path == '0':
            return None
        try:
            return local(path)
        except AppError as error:
            emit(str(error))
            emit('Input was not accepted. Correct the local file or cancel; no new page request was made.')

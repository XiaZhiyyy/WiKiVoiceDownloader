"""Server choice is separate from HTTP request normalization and group selection."""
from .errors import ParseError

def select_section(page, parser, context, ask, emit):
    page.request_context=context
    if not page.sections:
        if context.explicit_server:
            raise ParseError('missing_section: this adapter has no server text sections','missing_section')
        page.selected_section='legacy'; page.text_language=None
        return
    hint=(parser.fragment_section(context.fragment_hint) if context.fragment_hint and hasattr(parser,'fragment_section') else None)
    chosen=context.explicit_server or hint
    if context.explicit_server and context.fragment_hint and context.explicit_server!=hint:
        emit('--server 参数优先于链接锚点，实际选择：'+context.explicit_server)
    if not chosen:
        if context.fragment_hint: emit('无法识别链接锚点，请明确选择当前页面中实际存在的分区。')
        emit('\u8bf7\u5355\u9009\u670d\u52a1\u5668\u6587\u672c\u5206\u533a\uff1a')
        for index,section in enumerate(page.sections,1):
            emit(f'[{index}] {section.display_name} / {section.default_text_language}')
        while True:
            answer=ask('请输入一个分区序号：').strip()
            if answer.isascii() and answer.isdigit() and 1<=int(answer)<=len(page.sections):
                chosen=page.sections[int(answer)-1].section_id; break
            emit('服务器分区只能单选数字；A 仅用于分区内的语音分组。')
    section=next((s for s in page.sections if s.section_id==chosen),None)
    if section is None:
        raise ParseError(f'missing_section: requested server {chosen} is absent or not loaded; no fallback','missing_section')
    if not section.groups:
        raise ParseError(f'missing_section: {section.display_name} exists but contains no supported audio rows; no fallback','missing_section')
    page.groups=section.groups; page.selected_section=section.section_id
    page.text_language=section.default_text_language
    emit('\u670d\u52a1\u5668\u5206\u533a\uff1a'+section.display_name)
    emit('TXT \u6b63\u6587\u8bed\u8a00\uff1a'+str(page.text_language))
    if page.text_language=='ja':
        emit('\u65e5\u6587\u6b63\u6587\u5355\u72ec\u5bfc\u51fa\uff1b\u5df2\u83b7\u53d6\u7684\u9644\u5e26\u8bd1\u6587\u4fdd\u7559\u5728 metadata\u3002')

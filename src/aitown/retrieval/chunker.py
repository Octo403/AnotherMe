import re


def document_chunks(text: str, size=420, overlap=60):
    """Keep paragraphs together where possible; overlap only within a paragraph.

    Structured memories are handled as individual records by the corpus loader,
    never by concatenating adjacent messages or different speakers.
    """
    if not 0 <= overlap < size:
        raise ValueError("overlap must be smaller than size")
    current = ""
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > size:
            if current:
                yield current
                current = ""
            for start in range(0, len(paragraph), size - overlap):
                yield paragraph[start:start + size]
                if start + size >= len(paragraph):
                    break
        elif current and len(current) + len(paragraph) + 2 > size:
            yield current
            current = paragraph
        else:
            current = current + "\n\n" + paragraph if current else paragraph
    if current:
        yield current

import re

def filter_text(extracted_text):
    result = extracted_text
    result = result.replace("\r\n", "\n")
    result = result.replace("{", "")
    result = result.replace("\ufffd", "")
    result = re.sub(r"[''\u02bc\uFF07]", "'", result)
    result = re.sub(r"カ$", "", result)
    result = re.sub(r"987$", "?", result)
    result = re.sub(r"~", "\uFF5E", result)
    result = re.sub(r"^:", "", result)
    return result


def process_input_text(input_text):
    return filter_text(input_text)


def process_output_text(output_text):
    result = filter_text(output_text)

    # Cleanup duplicate or padded newlines
    result = re.sub(r"\n{2,}", "\n", result)
    result = re.sub(r"[ \t]*\n[ \t]*", "\n", result)

    return result

import re

def extract_from_text(text, media_type, config):
    """Extracts media URLs from raw text using regex."""
    extensions = getattr(config, f"{media_type.upper()}_EXTENSIONS", [])
    if not extensions:
        return []
    ext_list = [ext.lstrip('.') for ext in extensions]
    ext_pattern = '|'.join(ext_list)
    pattern = rf'((?:https?://|/)[^\s"\'<>\\,]+\.(?:{ext_pattern})(?:[^\s"\'<>\\,]*))'
    matches = re.findall(pattern, text, re.IGNORECASE)
    return [m.replace('\\/', '/') for m in matches]

def extract_urls_from_srcset(srcset_attr):
    """Extracts URLs and their sizes from a srcset attribute."""
    urls_with_sizes = []
    for part in srcset_attr.split(','):
        entry = part.strip().split(' ')
        url = entry[0]
        size = 0
        if len(entry) > 1:
            size_str = entry[1].lower()
            if size_str.endswith('w'):
                try: size = int(size_str[:-1])
                except: pass
            elif size_str.endswith('x'):
                try: size = int(float(size_str[:-1]) * 1000)
                except: pass
        if url:
            urls_with_sizes.append((url, size))
    return urls_with_sizes

def extract_url_from_style_background(style_attr):
    """Extracts URL from a background-image style attribute."""
    match = re.search(r'url\([\'\"]?(.*?)["\\]?\)', style_attr)
    return match.group(1).strip("'\"") if match else None

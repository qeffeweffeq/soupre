import re
import time
import random
import string


def random_sleep(min_seconds=1, max_seconds=5, human_like=True):
    """Sleep for a random amount of time within the given range or using human-like patterns"""
    if human_like:
        time.sleep(human_like_delay())
    else:
        time.sleep(random.uniform(min_seconds, max_seconds))


def clean_text(text):
    """Clean text while preserving meaningful whitespace and formatting"""
    if not text:
        return ""

    # Handle case where text is a list
    if isinstance(text, list):
        return ' '.join([clean_text(item) for item in text])

    # Convert to string if not already
    if not isinstance(text, str):
        text = str(text)

    # Convert non-breaking spaces to regular spaces
    text = text.replace('\xa0', ' ')

    # Normalize whitespace (but preserve paragraph breaks)
    text = re.sub(r'[\t ]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove leading/trailing whitespace
    text = text.strip()

    # Remove form input placeholders and labels that got mixed in
    text = re.sub(r'input type=.*?>', '', text)
    text = re.sub(r'<label.*?label>', '', text)

    return text


def sanitize_filename(filename):
    """Create a valid filename from a string"""
    # Remove invalid characters
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    sanitized = ''.join(c for c in filename if c in valid_chars)
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    # Limit length
    return sanitized[:100]


def human_like_delay():
    """Mimic human browsing patterns with natural timing variations"""
    # Base delay
    delay = random.normalvariate(2.5, 1.2)  # Normal distribution

    # Add occasional longer pauses (e.g., "reading" time)
    if random.random() < 0.2:  # 20% chance
        delay += random.uniform(4, 12)

    return max(0.5, delay)  # Ensure minimum delay


def is_meaningful_content(text, min_length=50):
    """Check if text contains meaningful content (not just navigation or footer text)"""
    # Clean the text
    text = clean_text(text)

    # Check length
    if len(text) < min_length:
        return False

    # Check for common non-content phrases
    non_content_phrases = [
        'cookie policy', 'privacy policy', 'terms of service',
        'all rights reserved', 'copyright', 'menu', 'navigation',
        'search', 'login', 'sign in', 'register'
    ]

    text_lower = text.lower()
    if any(phrase in text_lower for phrase in non_content_phrases) and len(text) < 200:
        return False

    # Count the ratio of links to text (high ratio suggests navigation)
    link_ratio = text.count('http') / (len(text) + 1)
    if link_ratio > 0.1:  # More than 10% of text is links
        return False

    return True


def get_element_attr_as_string(element, attr_name, default=''):
    """Safely get an element attribute as a string"""
    attr_value = element.get(attr_name, default)

    if isinstance(attr_value, list):
        return ' '.join(str(item) for item in attr_value)
    elif not isinstance(attr_value, str):
        return str(attr_value)

    return attr_value


def ensure_string(value):
    """Convert any value to a string safely"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Convert each item to string and join
        return ' '.join(ensure_string(item) for item in value)
    # For any other type, use str()
    return str(value)

import random
import time
from modules.config import logger, SCRAPE_DELAY_MIN, SCRAPE_DELAY_MAX

def random_delay():
    """
    Calculates a random float between SCRAPE_DELAY_MIN and SCRAPE_DELAY_MAX
    and pauses execution using time.sleep().
    Includes console logging of the wait time.
    """
    delay_time = random.uniform(SCRAPE_DELAY_MIN, SCRAPE_DELAY_MAX)
    logger.info(f"Waiting {delay_time:.2f}s before next request...")
    time.sleep(delay_time)

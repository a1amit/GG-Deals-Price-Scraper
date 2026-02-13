import json
import re
import os
import sys
import time
import threading
from difflib import SequenceMatcher
from queue import Queue, Empty
from urllib.parse import quote_plus

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


def _data_dir():
    """Writable directory: next to .exe when frozen, else script dir."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _data_dir()
GAMES_FILE = os.path.join(BASE_DIR, "games.txt")
RESULTS_FILE = os.path.join(BASE_DIR, "results.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")

# Global stop flag (used by CLI / backward compat)
_stop_requested = False
_lock = threading.Lock()


def request_stop():
    global _stop_requested
    _stop_requested = True


def reset_stop():
    global _stop_requested
    _stop_requested = False


def load_games(filepath=GAMES_FILE):
    with open(filepath, "r", encoding="utf-8") as f:
        games = [line.strip() for line in f if line.strip()]
    seen = set()
    unique = []
    for g in games:
        key = g.lower()
        if key not in seen:
            seen.add(key)
            unique.append(g)
    return unique


def update_progress(current, total, game_name, status="running", progress_file=None):
    data = {
        "current": current,
        "total": total,
        "game": game_name,
        "status": status,
        "percent": round((current / total) * 100, 1) if total > 0 else 0,
    }
    with _lock:
        with open(progress_file or PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)


def parse_price(price_str):
    if not price_str:
        return None
    cleaned = price_str.strip()
    if cleaned.lower() in ("free", "free to play"):
        return 0.0
    match = re.search(r"[\d]+[.,]?\d*", cleaned.replace(",", "."))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def normalize_name(name):
    """Normalize a game name for fuzzy comparison."""
    s = name.lower()
    # Remove common platform/edition suffixes
    s = re.sub(r'\b(ps[345]|xbox|switch|pc|mac|linux)\b', '', s)
    # Remove punctuation and extra whitespace
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def name_similarity(search_name, result_name):
    """Return a 0-1 similarity score between two game names."""
    a = normalize_name(search_name)
    b = normalize_name(result_name)
    # Exact normalized match
    if a == b:
        return 1.0
    # One contains the other fully
    if a in b or b in a:
        return max(0.85, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def save_results(results, output_file=None):
    with _lock:
        with open(output_file or RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)


def create_driver(headless=False):
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    if headless:
        options.add_argument("--headless=new")
    driver = uc.Chrome(options=options, headless=headless)
    return driver


def init_driver(driver):
    """Navigate to gg.deals and handle cookies/Cloudflare."""
    driver.get("https://gg.deals/")
    time.sleep(8)
    try:
        for sel in ["#onetrust-accept-btn-handler", "button[class*='cookie']", ".css-47sehv"]:
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                if btns and btns[0].is_displayed():
                    btns[0].click()
                    time.sleep(1)
                    break
            except Exception:
                pass
    except Exception:
        pass


def _extract_item_info(item):
    """Extract name, price and URL from a single search result item."""
    name = None
    price = None
    url = None

    try:
        link = item.find_element(By.CSS_SELECTOR, "a.full-link")
        aria = link.get_attribute("aria-label")
        if aria:
            name = aria.replace("Go to: ", "").strip()
        href = link.get_attribute("href")
        if href:
            url = href
    except NoSuchElementException:
        pass

    if not name:
        try:
            title_el = item.find_element(By.CSS_SELECTOR, ".game-info-title")
            name = title_el.text.strip()
        except NoSuchElementException:
            pass

    try:
        price_el = item.find_element(By.CSS_SELECTOR, ".price-inner")
        price = price_el.text.strip()
    except NoSuchElementException:
        pass

    return name, price, url


def _simplify_query(name):
    """Simplify a game name for a retry search (strip punctuation, subtitles)."""
    s = re.sub(r'[:\-–—|]', ' ', name)       # Replace colons/dashes with space
    s = re.sub(r'[^\w\s]', '', s)             # Remove remaining punctuation
    s = re.sub(r'\s+', ' ', s).strip()        # Collapse whitespace
    return s


def scrape_game(driver, game_name):
    """Search for a game on gg.deals and return best-matching result."""
    search_url = f"https://gg.deals/games/?title={quote_plus(game_name)}"
    driver.get(search_url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".hoverable-box, a.full-link, .game-info-title"))
        )
    except TimeoutException:
        time.sleep(3)

    best_name = None
    best_price = None
    best_url = None
    best_score = 0.0

    try:
        items = driver.find_elements(By.CSS_SELECTOR, ".hoverable-box")
        if not items:
            items = driver.find_elements(By.CSS_SELECTOR, "[class*='game-list'] > div, .list-items > div")

        # Check up to 8 results and pick the best match
        for item in items[:8]:
            name, price, url = _extract_item_info(item)
            if not name:
                continue
            score = name_similarity(game_name, name)
            if score > best_score:
                best_score = score
                best_name = name
                best_price = price
                best_url = url
            # Perfect or near-perfect match — stop early
            if score >= 0.95:
                break

        # Fallback: if no hoverable-box items, try raw selectors
        if best_name is None:
            links = driver.find_elements(By.CSS_SELECTOR, "a.full-link")
            for link in links[:8]:
                aria = link.get_attribute("aria-label")
                if not aria:
                    continue
                name = aria.replace("Go to: ", "").strip()
                score = name_similarity(game_name, name)
                if score > best_score:
                    best_score = score
                    best_name = name
                    best_url = link.get_attribute("href")
            prices = driver.find_elements(By.CSS_SELECTOR, ".price-inner")
            if prices and best_name:
                best_price = prices[0].text.strip()

    except Exception as e:
        print(f"    Selector error: {e}")

    # If no good match, retry with a simplified query (strips punctuation/subtitles)
    if best_score < 0.4:
        simplified = _simplify_query(game_name)
        if simplified.lower() != game_name.lower():
            retry_url = f"https://gg.deals/games/?title={quote_plus(simplified)}"
            try:
                driver.get(retry_url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".hoverable-box, a.full-link"))
                )
            except TimeoutException:
                time.sleep(3)

            try:
                items = driver.find_elements(By.CSS_SELECTOR, ".hoverable-box")
                for item in items[:8]:
                    name, price, url = _extract_item_info(item)
                    if not name:
                        continue
                    score = name_similarity(game_name, name)
                    if score > best_score:
                        best_score = score
                        best_name = name
                        best_price = price
                        best_url = url
                    if score >= 0.95:
                        break
            except Exception:
                pass

    # Reject matches that are too poor — avoids returning completely wrong games
    if best_score < 0.3:
        return None, None, None, 0.0

    return best_name, best_price, best_url, round(best_score, 3)


def _is_stopped(stop_event):
    """Check if stop was requested, supporting both Event objects and the global flag."""
    if stop_event is not None:
        return stop_event.is_set()
    return _stop_requested


def worker_fn(worker_id, task_queue, results_dict, total, counter, headless,
              output_file=None, progress_file=None, stop_event=None, label=""):
    """Worker thread: creates its own browser and processes games from the queue."""
    prefix = f"[{label}Worker {worker_id}]" if label else f"[Worker {worker_id}]"
    driver = None
    try:
        print(f"  {prefix} Launching browser...")
        driver = create_driver(headless=headless)
        init_driver(driver)
        print(f"  {prefix} Ready")

        while not _is_stopped(stop_event):
            try:
                idx, game_name = task_queue.get_nowait()
            except Empty:
                break

            try:
                matched_name, price, game_url, confidence = scrape_game(driver, game_name)
            except Exception as e:
                print(f"  {prefix} Error on '{game_name}': {e}")
                matched_name, price, game_url, confidence = None, None, None, 0.0
                try:
                    driver.get("https://gg.deals/")
                    time.sleep(3)
                except Exception:
                    pass

            result = {
                "search_name": game_name,
                "matched_name": matched_name or game_name,
                "price": price,
                "price_value": parse_price(price) if price else None,
                "url": game_url,
                "match_confidence": confidence,
            }
            results_dict[idx] = result

            with _lock:
                counter[0] += 1
                done = counter[0]
            print(f"  {prefix} [{done}/{total}] {game_name} -> {price or 'N/A'}")
            update_progress(done, total, game_name, "running", progress_file=progress_file)

            # Save after each game
            ordered = [results_dict[i] for i in sorted(results_dict.keys())]
            save_results(ordered, output_file=output_file)

            task_queue.task_done()
            time.sleep(0.3)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        print(f"  {prefix} Shut down")


def scrape_prices(headless=False, games_list=None, workers=3, output_file=None,
                  progress_file=None, stop_event=None, label=""):
    if stop_event is None:
        reset_stop()
    else:
        stop_event.clear()

    games = games_list if games_list else load_games()
    total = len(games)

    update_progress(0, total, "", "starting", progress_file=progress_file)

    # Clamp workers: at least 1, at most the number of games
    workers = max(1, min(workers, total))
    print(f"Starting {workers} worker(s) for {total} games... {label}")

    # Shared state
    task_queue = Queue()
    results_dict = {}       # idx -> result (thread-safe dict writes by distinct keys)
    counter = [0]           # mutable counter wrapped in list

    for i, game in enumerate(games):
        task_queue.put((i, game))

    threads = []
    for wid in range(workers):
        t = threading.Thread(
            target=worker_fn,
            args=(wid + 1, task_queue, results_dict, total, counter, headless),
            kwargs=dict(output_file=output_file, progress_file=progress_file,
                        stop_event=stop_event, label=label),
            daemon=True,
        )
        t.start()
        threads.append(t)
        # Stagger launches so Chrome instances don't collide
        if wid < workers - 1:
            time.sleep(4)

    for t in threads:
        t.join()

    # Build final ordered results
    results = [results_dict[i] for i in sorted(results_dict.keys())]

    if _is_stopped(stop_event):
        update_progress(counter[0], total, "", "stopped", progress_file=progress_file)
    else:
        update_progress(total, total, "", "completed", progress_file=progress_file)

    save_results(results, output_file=output_file)
    return results


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    w = 3
    for arg in sys.argv:
        if arg.startswith("--workers="):
            w = int(arg.split("=")[1])
    print(f"Starting scraper (headless={headless}, workers={w})...")
    results = scrape_prices(headless=headless, workers=w)
    print(f"\nDone! Scraped {len(results)} games.")
    found = sum(1 for r in results if r["price"])
    print(f"Found prices for {found}/{len(results)} games.")

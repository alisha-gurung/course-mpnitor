import os
import sys
import argparse
import requests
from bs4 import BeautifulSoup

# Helper to load simple .env file if it exists locally
def load_env():
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    key, val = line.strip().split("=", 1)
                    os.environ[key.strip()] = val.strip()

load_env()

# Force stdout/stderr to use UTF-8 encoding to avoid Windows UnicodeEncodeErrors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Define course page and resolve ntfy topic name
COURSE_URL = os.environ.get("COURSE_URL", "").strip()
DEFAULT_NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

def parse_availability(html_content):
    """
    Parses the course page HTML and returns a dictionary of class details:
    { class_number: { 'available': int, 'section': str, 'size': str } }
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    sessions = soup.find_all(class_='cmp-course-accordion--container-session')
    
    results = {}
    for session in sessions:
        cards = session.find_all(class_='cmp-course-accordion--card')
        class_number = None
        available = None
        section = None
        size = None
        
        for card in cards:
            text = card.get_text(separator=' ', strip=True)
            if 'Class number' in text:
                class_number = text.replace('Class number', '').strip()
            elif 'Available' in text:
                available = text.replace('Available', '').strip()
            elif 'Section' in text:
                section = text.replace('Section', '').strip()
            elif 'Size' in text:
                size = text.replace('Size', '').strip()
                
        if class_number:
            try:
                available_count = int(available)
            except (ValueError, TypeError):
                available_count = 0
                
            results[class_number] = {
                'available': available_count,
                'section': section or "Unknown",
                'size': size or "Unknown"
            }
            
    return results

def send_notification(topic, title, message, priority="default", tags="bell"):
    """
    Publishes a notification to ntfy.sh
    """
    url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags
    }
    try:
        response = requests.post(url, data=message.encode('utf-8'), headers=headers, timeout=10)
        if response.status_code == 200:
            print(f"Successfully sent notification to topic: {topic}")
        else:
            print(f"Failed to send notification. HTTP Status: {response.status_code}, Body: {response.text}")
    except Exception as e:
        print(f"Error sending notification: {e}")

def monitor_classes(topic, target_classes):
    """
    Fetches the course page, checks the targeted classes, and sends alerts if available.
    """
    print(f"Fetching course page: {COURSE_URL}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(COURSE_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch course page: {e}")
        # Notify user about check failure so they know if the monitor breaks
        send_notification(
            topic,
            title="Monitor Error",
            message=f"Failed to check course page: {e}",
            priority="low",
            tags="warning"
        )
        return

    classes = parse_availability(response.text)
    
    available_alerts = []
    
    for class_num in target_classes:
        if class_num in classes:
            details = classes[class_num]
            status_msg = f"Class {class_num} ({details['section']}): {details['available']}/{details['size']} seats available."
            print(status_msg)
            
            if details['available'] > 0:
                available_alerts.append(
                    f"🟢 Class {class_num} ({details['section']}) has {details['available']} seat(s) available! (Size: {details['size']})"
                )
        else:
            print(f"Warning: Target class {class_num} not found on the page.")
            
    if available_alerts:
        alert_title = "CLASS AVAILABLE!"
        alert_message = "\n".join(available_alerts) + f"\n\nLink: {COURSE_URL}"
        print(f"Alerting! {alert_message}")
        send_notification(
            topic=topic,
            title=alert_title,
            message=alert_message,
            priority="high",
            tags="bell,warning,loud_sound"
        )
    else:
        print("No targeted classes are currently available.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Monitor university course class availability.")
    parser.add_argument("--test-notify", action="store_true", help="Send a test notification to verify ntfy setup.")
    parser.add_argument("--topic", default=DEFAULT_NTFY_TOPIC, help="The ntfy.sh topic to publish to.")
    args = parser.parse_args()
    
    if not args.topic:
        print("Error: No ntfy topic specified. Please set the 'NTFY_TOPIC' environment variable, create a '.env' file containing 'NTFY_TOPIC=...', or pass the --topic argument.", file=sys.stderr)
        sys.exit(1)

    if not COURSE_URL:
        print("Error: No course URL specified. Please set the 'COURSE_URL' environment variable or create a '.env' file containing 'COURSE_URL=...'.", file=sys.stderr)
        sys.exit(1)
        
    env_classes = os.environ.get("TARGET_CLASSES")
    if not env_classes or not env_classes.strip():
        print("Error: No target classes specified. Please set the 'TARGET_CLASSES' environment variable (comma-separated) or create a '.env' file containing 'TARGET_CLASSES=...'.", file=sys.stderr)
        sys.exit(1)
        
    target_classes = [c.strip() for c in env_classes.split(",") if c.strip()]
    
    if args.test_notify:
        print(f"Sending test notification to topic: {args.topic}")
        send_notification(
            topic=args.topic,
            title="Course Monitor Test",
            message="Test notification from your course availability monitor. It works! 🎉",
            priority="default",
            tags="tada,bell"
        )
    else:
        monitor_classes(args.topic, target_classes)

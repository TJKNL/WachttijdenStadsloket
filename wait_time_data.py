import requests
import mysql.connector
import re
from datetime import datetime
import pytz

def create_database(config):
    """
    Create the database if it doesn't exist.
    This function accepts a dictionary configuration.
    """
    # We assume the database already exists since you mentioned it
    # Just validate the config to make sure it's properly formatted
    required_keys = ['host', 'user', 'password', 'database']
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required configuration key: {key}")
    
    # If using connection string elsewhere, handle accordingly
    return True

class WaitTimeLib:
    def __init__(self, config):
        """Initialize database connection with config dictionary"""
        self.host = config['host']
        self.user = config['user']
        self.password = config['password']
        self.database = config['database']
        self.db_config = config
        self.db = mysql.connector.connect(**config)
        self.cursor = self.db.cursor()
        self.timezone = pytz.timezone('Europe/Amsterdam')
        self.create_table()
        
    def create_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS wait_times (
            id INT AUTO_INCREMENT PRIMARY KEY,
            stadsloket_id INT NOT NULL,
            waiting INT,
            waittime VARCHAR(255),
            timestamp DATETIME,
            INDEX idx_stadsloket_id (stadsloket_id)
        )
        """)

    def create_loket_names_table(self):
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS loket_names (
            stadsloket_id INT NOT NULL,
            loket_name VARCHAR(255),
            PRIMARY KEY (stadsloket_id),
            CONSTRAINT fk_stadsloket
                FOREIGN KEY (stadsloket_id)
                REFERENCES wait_times(stadsloket_id)
        )
        """)

    def fetch_data(self):
        response = requests.get('https://wachttijdenamsterdam.nl/data/')
        return response.json()

    def parse_waittime(self, waittime_str):
        if not waittime_str or waittime_str.lower().startswith('geen'):
            return 0
        if 'uur' in waittime_str.lower():
            return 70
        # Remove ' minuten'
        numeric = ''.join([c for c in waittime_str if c.isdigit()])
        if numeric.isdigit():
            val = int(numeric)
            return val if val <= 60 else 70
        return 0

    def store_data(self, data):
        for entry in data:
            parsed_waittime = self.parse_waittime(entry['waittime'])
            # Get current time in Amsterdam timezone
            current_time = datetime.now(self.timezone)
            self.cursor.execute("""
            INSERT INTO wait_times (stadsloket_id, waiting, waittime, timestamp)
            VALUES (%s, %s, %s, %s)
            """, (entry['id'], entry['waiting'], parsed_waittime, current_time))
        self.db.commit()

    def get_mean_wait_times(self):
        self.cursor.execute("""
            SELECT wt.stadsloket_id, ln.loket_name, AVG(wt.waiting) as mean_waiting
            FROM wait_times wt
            LEFT JOIN loket_names ln
            ON wt.stadsloket_id = ln.stadsloket_id
            GROUP BY wt.stadsloket_id, ln.loket_name
        """)
        rows = self.cursor.fetchall()
        results = []
        for stadsloket_id, loket_name, mean_waiting in rows:
            # Convert the decimal or None value to an integer
            results.append((stadsloket_id, loket_name or 'Unknown', int(mean_waiting or 0)))
        return results

    def get_raw_data(self):
        self.cursor.execute("""
            SELECT wt.stadsloket_id, ln.loket_name, wt.waiting, wt.waittime, wt.timestamp
            FROM wait_times wt
            LEFT JOIN loket_names ln
            ON wt.stadsloket_id = ln.stadsloket_id
        """)
        rows = self.cursor.fetchall()
        results = []
        for sid, name, waiting, wtime, ts in rows:
            results.append((sid, name or 'Unknown', waiting, wtime, ts))
        return results

    def fetch_loket_names(self):
        # Retrieve the main page HTML
        page_response = requests.get('https://wachttijdenamsterdam.nl')
        page_html = page_response.text
        # Simple regular expression to capture (stadsloket name) + (id from nfwrtXX)
        # Each row has the pattern: <td data-title="Stadsloket"> X </td> ... id="nfwrtY"
        matches = re.findall(r'<td data-title="Stadsloket">\s*(.*?)</td>.*?id="nfwrt(\d+)"',
                             page_html, flags=re.DOTALL)
        # Create table if needed
        self.create_loket_names_table()
        # Store results
        for (name, loket_id) in matches:
            self.cursor.execute("""
            INSERT INTO loket_names (stadsloket_id, loket_name)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                loket_name = VALUES(loket_name)
            """, (loket_id, name.strip()))
        self.db.commit()

    def get_current_waiting(self):
        self.cursor.execute("""
            SELECT wt.stadsloket_id, ln.loket_name, wt.waittime, wt.waiting
            FROM wait_times wt
            LEFT JOIN loket_names ln ON wt.stadsloket_id = ln.stadsloket_id
            WHERE wt.timestamp = (
                SELECT MAX(timestamp)
                FROM wait_times
                WHERE stadsloket_id = wt.stadsloket_id
            )
        """)
        return [(sid, name or 'Unknown', waittime, waiting) for sid, name, waittime, waiting in self.cursor.fetchall()]

    def get_hourly_averages(self):
        """Get average wait times in minutes by hour of day for each stadsloket"""
        self.cursor.execute("""
            SELECT 
                wt.stadsloket_id,
                ln.loket_name,
                HOUR(wt.timestamp) as hour_of_day,
                AVG(wt.waittime) as avg_waittime
            FROM wait_times wt
            LEFT JOIN loket_names ln ON wt.stadsloket_id = ln.stadsloket_id
            WHERE HOUR(wt.timestamp) BETWEEN 8 AND 18
            GROUP BY wt.stadsloket_id, ln.loket_name, HOUR(wt.timestamp)
            ORDER BY wt.stadsloket_id, HOUR(wt.timestamp)
        """)
        
        results = {}
        hours = list(range(8, 19))  # 8:00 to 18:00
        
        for stadsloket_id, loket_name, hour, avg_waittime in self.cursor.fetchall():
            if loket_name not in results:
                results[loket_name or f'Unknown-{stadsloket_id}'] = {
                    'label': loket_name or f'Unknown-{stadsloket_id}',
                    'data': [0] * len(hours)
                }
            try:
                hour_index = hours.index(hour)
                results[loket_name or f'Unknown-{stadsloket_id}']['data'][hour_index] = round(float(avg_waittime or 0), 1)
            except (ValueError, IndexError):
                pass
                
        return {
            'labels': [f"{h}:00" for h in hours],
            'datasets': list(results.values())
        }

    def get_last_update_time(self):
        """Get the timestamp of the most recent data update"""
        self.cursor.execute("""
            SELECT MAX(timestamp)
            FROM wait_times
        """)
        result = self.cursor.fetchone()
        return result[0] if result and result[0] else None

    def close(self):
        self.cursor.close()
        self.db.close()

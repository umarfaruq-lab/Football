import os
import requests
from datetime import datetime, timedelta
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Environment Variables injected securely from GitHub Secrets
API_KEY = os.getenv("FOOTBALL_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY} if API_KEY else {}

LEAGUES = {
    "PL": "Premier League (England)",
    "PD": "La Liga (Spain)",
    "SA": "Serie A (Italy)",
    "BL1": "Bundesliga (Germany)",
    "FL1": "Ligue 1 (France)"
}

TRACKER_FILE = "tracker.csv"

def fetch_todays_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"{BASE_URL}/matches?dateFrom={today}&dateTo={today}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Error fetching matches: {response.status_code}")
        return []
    matches = response.json().get("matches", [])
    return [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]

def fetch_league_standings(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/standings"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return {}
    standings_data = response.json().get("standings", [])
    for table in standings_data:
        if table.get("type") == "TOTAL":
            return {item["team"]["id"]: item for item in table.get("table", [])}
    return {}

def fetch_h2h_data(match_id):
    url = f"{BASE_URL}/matches/{match_id}/head2head"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return None
    return response.json().get("aggregates", {})

def parse_form_score(form_str):
    """Calculates momentum points from recent form string (e.g. 'W,W,D,L,W')"""
    if not form_str:
        return 0
    score = 0
    cleaned = form_str.replace(",", "")
    for char in cleaned:
        if char == 'W': score += 3
        elif char == 'D': score += 1
        elif char == 'L': score -= 1
    return score

def analyze_and_predict(matches):
    predictions = []
    
    for match in matches:
        match_id = match["id"]
        comp_code = match["competition"]["code"]
        home_team = match["homeTeam"]["name"]
        away_team = match["awayTeam"]["name"]
        home_id = match["homeTeam"]["id"]
        away_id = match["awayTeam"]["id"]
        
        # 1. Fetch Standings & Form Data
        standings = fetch_league_standings(comp_code)
        home_stats = standings.get(home_id, {})
        away_stats = standings.get(away_id, {})
        
        home_points = home_stats.get("points", 0)
        away_points = away_stats.get("points", 0)
        home_position = home_stats.get("position", 10)
        away_position = away_stats.get("position", 10)
        
        home_form_str = home_stats.get("form", "")
        away_form_str = away_stats.get("form", "")
        home_form_pts = parse_form_score(home_form_str)
        away_form_pts = parse_form_score(away_form_str)
        
        # 2. Fetch Head-to-Head (H2H) Data
        h2h = fetch_h2h_data(match_id)
        home_h2h_wins = 0
        away_h2h_wins = 0
        if h2h:
            home_h2h_wins = h2h.get("homeTeam", {}).get("wins", 0)
            away_h2h_wins = h2h.get("awayTeam", {}).get("wins", 0)
            
        # 3. Weighted Scoring Algorithm (Standings + Form + H2H + Home Advantage)
        home_score = (20 - home_position) * 1.2 + (home_points * 0.3) + (home_form_pts * 0.8) + (home_h2h_wins * 0.5) + 3.0
        away_score = (20 - away_position) * 1.2 + (away_points * 0.3) + (away_form_pts * 0.8) + (away_h2h_wins * 0.5)
        
        if home_score > away_score + 6:
            predicted_winner = home_team
            confidence = "High"
        elif away_score > home_score + 6:
            predicted_winner = away_team
            confidence = "Moderate"
        else:
            predicted_winner = "Draw / Closely Contested"
            confidence = "Low"
            
        predictions.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "league": LEAGUES.get(comp_code, comp_code),
            "match": f"{home_team} vs {away_team}",
            "prediction": predicted_winner,
            "confidence": confidence,
            "home_team": home_team,
            "away_team": away_team
        })
        
    return predictions

def update_tracker_and_evaluate(new_predictions):
    """Grades past pending predictions and appends new ones to tracker.csv"""
    if os.path.exists(TRACKER_FILE):
        df = pd.read_csv(TRACKER_FILE)
    else:
        df = pd.DataFrame(columns=["Date", "League", "Match", "Prediction", "Actual", "Status"])
    
    # Grade past PENDING predictions by checking match results from recent days
    pending_rows = df[df["Status"] == "PENDING"]
    if not pending_rows.empty:
        oldest_date = pending_rows["Date"].min()
        today_str = datetime.now().strftime("%Y-%m-%d")
        url = f"{BASE_URL}/matches?dateFrom={oldest_date}&dateTo={today_str}"
        response = requests.get(url, headers=HEADERS)
        
        if response.status_code == 200:
            finished_matches = response.json().get("matches", [])
            for idx, row in pending_rows.iterrows():
                match_desc = row["Match"]
                # Find matching finished game
                for m in finished_matches:
                    if m.get("status") == "FINISHED":
                        h_name = m["homeTeam"]["name"]
                        a_name = m["awayTeam"]["name"]
                        if f"{h_name} vs {a_name}" == match_desc:
                            full_time = m["score"]["fullTime"]
                            h_goals = full_time.get("home", 0)
                            a_goals = full_time.get("away", 0)
                            
                            if h_goals > a_goals:
                                actual_winner = h_name
                            elif a_goals > h_name: # typo safe check
                                actual_winner = a_name
                            else:
                                actual_winner = "Draw / Closely Contested"
                            if h_goals == a_goals:
                                actual_winner = "Draw / Closely Contested"
                                
                            df.loc[idx, "Actual"] = f"{h_name} {h_goals}-{a_goals} {a_name}"
                            
                            if row["Prediction"] == actual_winner:
                                df.loc[idx, "Status"] = "WON"
                            else:
                                df.loc[idx, "Status"] = "LOST"
                            break

    # Append new predictions
    new_rows = []
    for p in new_predictions:
        # Check if match already exists in tracker for today
        existing = df[(df["Date"] == p["date"]) & (df["Match"] == p["match"])]
        if existing.empty:
            new_rows.append({
                "Date": p["date"],
                "League": p["league"],
                "Match": p["match"],
                "Prediction": p["prediction"],
                "Actual": "Pending",
                "Status": "PENDING"
            })
            
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        df = pd.concat([df, new_df], ignore_index=True)
        
    df.to_csv(TRACKER_FILE, index=False)
    return df

def send_email(predictions, tracker_df):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing. Printing summary to console:")
        for p in predictions:
            print(p)
        return

    # Calculate historical accuracy stats
    completed = tracker_df[tracker_df["Status"].isin(["WON", "LOST"])]
    total_completed = len(completed)
    total_won = len(completed[completed["Status"] == "WON"])
    accuracy_pct = (total_won / total_completed * 100) if total_completed > 0 else 0.0

    html_content = "<h2>⚽ Advanced Football Predictions & Accuracy Tracker</h2>"
    html_content += f"<p>Analysis generated on <b>{datetime.now().strftime('%Y-%m-%d')}</b> utilizing Standings, Recent Form, and H2H History.</p>"
    
    # Accuracy Banner
    html_content += f"<div style='background: #e9ecef; padding: 10px; border-radius: 5px; margin-bottom: 15px;'>"
    html_content += f"<b>Model Track Record:</b> {total_won} Won / {total_completed} Completed ({accuracy_pct:.1f}% Success Rate)"
    html_content += "</div>"

    high_prob_teams = []

    html_content += "<h3>📅 Today's Fixtures & Predictions</h3>"
    html_content += "<table border='1' cellspacing='0' cellpadding='8' style='border-collapse: collapse; width: 100%; font-family: sans-serif; font-size: 13px;'>"
    html_content += "<tr style='background-color: #f8f9fa;'><th>League</th><th>Matchup</th><th>Predicted Winner</th><th>Confidence</th></tr>"
    
    for p in predictions:
        html_content += f"<tr><td>{p['league']}</td><td>{p['match']}</td><td><b>{p['prediction']}</b></td><td>{p['confidence']}</td></tr>"
        if p['confidence'] == 'High':
            high_prob_teams.append(f"<b>{p['prediction']}</b> ({p['match']})")
            
    html_content += "</table>"
    
    html_content += "<h3>🏆 Shortlist: High Probability Winners</h3><ul>"
    if high_prob_teams:
        for team in high_prob_teams:
            html_content += f"<li>{team}</li>"
    else:
        html_content += "<li>No high-confidence mismatches today; expect balanced games.</li>"
    html_content += "</ul>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Advanced Football Predictions & Tracker - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Advanced prediction email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    print("Fetching matches and analyzing Form & H2H...")
    matches = fetch_todays_matches()
    print(f"Found {len(matches)} matches scheduled for today.")
    
    predictions = analyze_and_predict(matches)
    tracker_df = update_tracker_and_evaluate(predictions)
    send_email(predictions, tracker_df)
    print("Pipeline run and tracker update finished.")

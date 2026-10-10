import os
import requests
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Environment Variables
API_KEY = os.getenv("FOOTBALL_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

# Top 5 European League Codes supported by Football-Data.org free tier
LEAGUES = {
    "PL": "Premier League (England)",
    "PD": "La Liga (Spain)",
    "SA": "Serie A (Italy)",
    "BL1": "Bundesliga (Germany)",
    "FL1": "Ligue 1 (France)"
}

def fetch_todays_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"{BASE_URL}/matches?dateFrom={today}&dateTo={today}"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f"Error fetching matches: {response.status_code} - {response.text}")
        return []
        
    matches = response.json().get("matches", [])
    # Filter exclusively for top 5 leagues
    filtered_matches = [m for m in matches if m["competition"]["code"] in LEAGUES]
    return filtered_matches

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

def analyze_and_predict(matches):
    predictions = []
    
    for match in matches:
        comp_code = match["competition"]["code"]
        home_team = match["homeTeam"]["name"]
        away_team = match["awayTeam"]["name"]
        home_id = match["homeTeam"]["id"]
        away_id = match["awayTeam"]["id"]
        
        standings = fetch_league_standings(comp_code)
        home_stats = standings.get(home_id, {})
        away_stats = standings.get(away_id, {})
        
        home_points = home_stats.get("points", 0)
        away_points = away_stats.get("points", 0)
        home_position = home_stats.get("position", 10)
        away_position = away_stats.get("position", 10)
        
        # Algorithmic weighting based on league position difference & points + home advantage
        home_score = (20 - home_position) * 1.5 + (home_points * 0.4) + 3.0
        away_score = (20 - away_position) * 1.5 + (away_points * 0.4)
        
        if home_score > away_score + 5:
            predicted_winner = home_team
            confidence = "High"
        elif away_score > home_score + 5:
            predicted_winner = away_team
            confidence = "Moderate"
        else:
            predicted_winner = "Draw / Closely Contested"
            confidence = "Low"
            
        predictions.append({
            "league": LEAGUES.get(comp_code, comp_code),
            "match": f"{home_team} vs {away_team}",
            "prediction": predicted_winner,
            "confidence": confidence
        })
        
    return predictions

def send_email(predictions):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email configuration missing. Printing text summary to console:")
        print(predictions)
        return

    html_content = "<h2>⚽ Daily European Football Predictions</h2>"
    html_content += f"<p>Automated match analysis for <b>{datetime.now().strftime('%Y-%m-%d')}</b> across Europe's top leagues.</p>"
    
    high_prob_teams = []

    html_content += "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse: collapse; width: 100%; font-family: sans-serif;'>"
    html_content += "<tr style='background-color: #f8f9fa;'><th>League</th><th>Matchup</th><th>Predicted Outcome</th><th>Confidence</th></tr>"
    
    for p in predictions:
        html_content += f"<tr><td>{p['league']}</td><td>{p['match']}</td><td><b>{p['prediction']}</b></td><td>{p['confidence']}</td></tr>"
        if p['confidence'] == 'High':
            high_prob_teams.append(f"<b>{p['prediction']}</b> ({p['match']})")
            
    html_content += "</table>"
    
    html_content += "<h3>🏆 Final Shortlist: High Probability Winners</h3><ul>"
    if high_prob_teams:
        for team in high_prob_teams:
            html_content += f"<li>{team}</li>"
    else:
        html_content += "<li>No high-confidence mismatches found today; expect tight games.</li>"
    html_content += "</ul>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Football Predictions & Analysis - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Prediction report email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    print("Fetching matches for top European leagues...")
    matches = fetch_todays_matches()
    print(f"Found {len(matches)} matches scheduled for today.")
    
    predictions = analyze_and_predict(matches)
    send_email(predictions)
    print("Pipeline run finished.")

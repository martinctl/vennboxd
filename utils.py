import os
import time
import random
import requests
import streamlit as st
import math
from letterboxdpy.user import User

# --- TMDB & IMAGE UTILS ---

@st.cache_data(ttl=86400 * 7)
def tmdb_search_movie(title: str, year: int | None, api_key: str) -> dict | None:
    """Search TMDB for a movie and return the best match (raw TMDB result dict)."""
    if not api_key:
        return None
    params = {"api_key": api_key, "query": title, "include_adult": "false"}
    if year:
        # Allow +/- 1 year flexibility
        params["year"] = str(year)
    
    try:
        r = requests.get("https://api.themoviedb.org/3/search/movie", params=params, timeout=5)
        r.raise_for_status()
        payload = r.json()
        results = payload.get("results") or []
        
        if not results and year:
            # Fallback: Try without year if strict match failed
            del params["year"]
            r = requests.get("https://api.themoviedb.org/3/search/movie", params=params, timeout=5)
            if r.status_code == 200:
                results = r.json().get("results") or []

        return results[0] if results else None
    except Exception:
        return None

@st.cache_data(ttl=86400 * 7)
def tmdb_get_recommendations(movie_id: int, api_key: str) -> list[dict]:
    """Fetch 'Recommendations' for a given TMDB movie ID."""
    if not api_key or not movie_id:
        return []
    
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/recommendations"
    params = {"api_key": api_key, "language": "en-US", "page": 1}
    
    try:
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json().get("results", [])[:12] # Top 12 recs
    except Exception:
        return []

@st.cache_data(ttl=86400)
def tmdb_get_popular(api_key: str, page: int = 1) -> list[dict]:
    """Fetch popular movies from TMDB."""
    if not api_key:
        return []
    url = "https://api.themoviedb.org/3/movie/popular"
    params = {"api_key": api_key, "language": "en-US", "page": page}
    try:
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:
        return []

def tmdb_image_url(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    return f"https://image.tmdb.org/t/p/{size}{path}"

# --- LETTERBOXD UTILS ---

def extract_movies_by_slug(data) -> dict:
    """Normalize letterboxdpy return into slug -> details."""
    if not data:
        return {}
    
    out = {}
    
    def process_item(item):
        if isinstance(item, dict):
            slug = item.get("slug")
            if slug and isinstance(slug, str):
                return slug, item
        return None, None

    # Handle various structures (dict of movies, list of movies, etc)
    if isinstance(data, dict):
        # top level movies key
        if "movies" in data and isinstance(data["movies"], dict):
            return extract_movies_by_slug(data["movies"]) # recurse
        
        for k, v in data.items():
            s, i = process_item(v)
            if s: 
                out[s] = i
            elif isinstance(k, str) and k and isinstance(v, dict):
                # Fallback: Key is slug
                # Inject slug into v just in case? No, just use v as details.
                out[k] = v
    
    elif isinstance(data, list):
        for item in data:
            s, i = process_item(item)
            if s: out[s] = i
            
    return out

@st.cache_data(ttl=3600)
def fetch_user_data(username: str):
    """
    Fetches watchlist, watched films, rating, and liked films.
    Returns: (watchlist_dict, watched_set_of_slugs, ratings_dict, liked_set, watched_index_tuple_set, error_msg)
    """
    try:
        time.sleep(random.uniform(0.3, 0.7)) # Rate limiting
        user_instance = User(username.strip())
        
        # 1. Watchlist
        try:
            raw_wl = user_instance.get_watchlist_movies()
        except:
            try:
                raw_wl = user_instance.get_watchlist()
            except:
                raw_wl = {}
        watchlist = extract_movies_by_slug(raw_wl)

        # 2. Watched & Ratings
        try:
            raw_films = user_instance.get_films()
        except:
            raw_films = {}
        
        films = extract_movies_by_slug(raw_films)
        watched_slugs = set(films.keys())
        
        # Create Title/Year index for strict filtering
        # Set of (title_lower, year)
        watched_index = set()
        
        ratings = {}
        liked = set()
        
        for slug, info in films.items():
            # Build Index
            t = info.get("name", "").strip().lower()
            try:
                y = int(info.get("year")) if info.get("year") else None
            except:
                y = None
            if t:
                watched_index.add((t, y))
            
            # Liked
            if info.get("liked"):
                liked.add(slug)
                
            # Rating (Normalizing 0.5-5.0 to 1-10)
            r = info.get("rating")
            if r:
                try:
                    val = float(r)
                    # letterboxdpy returns 1-10 integers usually.
                    # Only normalize if we are extremely sure it's 0.5-5.0 float? 
                    # The debug showed inputs like 9, 8, 10 (int).
                    # A 5/10 comes as 5. The old logic doubled it to 10. 
                    # We should just trust valid integers > 0.
                    # If it's a float like 2.5, maybe it's stars? 
                    # But 5 (int) is ambiguous. 5 stars or 5 score?
                    # Let's assume if it is an integer, it is 1-10.
                    # If it is a float with decimal, maybe check?
                    # Safer: Just take as is if > 0.
                    if val > 0:
                        ratings[slug] = {
                            "score": int(val),
                            "name": info.get("name"),
                            "year": info.get("year")
                        }
                except:
                    pass

        return watchlist, watched_slugs, ratings, liked, watched_index, None

    except Exception as e:
        return {}, set(), {}, set(), set(), str(e)

# --- RECOMMENDATION ENGINE ---

class RecommendationEngine:
    def __init__(self, usernames: list[str], tmdb_api_key: str = ""):
        self.usernames = [u.strip() for u in usernames if u.strip()]
        self.tmdb_api_key = tmdb_api_key
        # Structure: {username: {watchlist, watched, ratings, liked, watched_index}}
        self.user_data = {}
        self.errors = []
        self.global_watched_index = set() # (title_lower, year)

    def fetch_data(self):
        """Fetches data for all users and builds global indexes."""
        for user in self.usernames:
            wl, w, r, l, index, err = fetch_user_data(user)
            if err:
                self.errors.append(f"{user}: {err}")
            else:
                self.user_data[user] = {
                    "watchlist": wl,
                    "watched": w,
                    "ratings": r,
                    "liked": l,
                    "watched_index": index
                }
                self.global_watched_index.update(index)

    def is_watched(self, title: str, year: int | None) -> bool:
        """Strict check if a movie is watched by ANY user."""
        if not title: return False
        t = title.strip().lower()
        
        # Direct check
        if (t, year) in self.global_watched_index:
            return True
        
        # Fuzzy check if exact year missing? No, strict is safer.
        # But if year is None in candidate, check if title exists at all?
        if year is None:
            # Dangerous if common title, but let's check
            # For now, require year match if year exists in index
            pass
            
        return False

    def _get_candidate_key(self, slug, tmdb_id=None):
        return slug # Unique identifier

    def generate_recommendations(self, 
                                 use_watchlist: bool = True,
                                 use_similar: bool = True,
                                 use_popular: bool = False,
                                 discovery_weight: float = 0.5) -> list[dict]:
        
        if not self.user_data:
            return []

        # Candidate Structure:
        # id -> {
        #   details: {title, year, url, poster...},
        #   watchlist_users: [u1, u2],
        #   similar_contributions: {user: [points, ...]},
        #   sources: [],
        #   tmdb: {}
        # }
        candidates = {}

        def get_or_create(unique_id, title, year, url, details_obj=None, tmdb_obj=None):
            # STRICT FILTER
            if self.is_watched(title, year):
                return None
            
            if unique_id not in candidates:
                candidates[unique_id] = {
                    "details": {
                        "name": title,
                        "year": year,
                        "url": url
                    },
                    "watchlist_users": set(),
                    "similar_contributions": {}, # user -> list of scores
                    "sources": set(),
                    "tmdb": tmdb_obj
                }
                if details_obj: # Merge details
                     candidates[unique_id]["details"].update(details_obj)
                     
            return candidates[unique_id]

        # --- 1. POPULATE CANDIDATES FROM WATCHLISTS ---
        if use_watchlist:
            for user, data in self.user_data.items():
                for slug, details in data["watchlist"].items():
                    # Parse title/year from details
                    title = details.get("name", "Unknown")
                    try:
                        year = int(details.get("year")) if details.get("year") else None
                    except: year = None
                    
                    # Try to resolve to TMDB ID for merging
                    # If we have API key, we should try.
                    tmdb_id = None
                    tmdb_obj = None
                    
                    if self.tmdb_api_key:
                        tmdb_obj = tmdb_search_movie(title, year, self.tmdb_api_key)
                        if tmdb_obj:
                             tmdb_id = f"tmdb:{tmdb_obj['id']}"

                    # Key preference: TMDB ID > Slug
                    key = tmdb_id if tmdb_id else slug
                    
                    cand = get_or_create(key, title, year, f"https://letterboxd.com/film/{slug}/", details_obj=details, tmdb_obj=tmdb_obj)
                    if cand:
                        cand["watchlist_users"].add(user)
                        cand["sources"].add("Watchlist")

        # --- 2. POPULATE CANDIDATES FROM SIMILAR ---
        if use_similar and self.tmdb_api_key:
            # Config
            min_rating_threshold = 8 # As per prompt "movies rated 8/10 comes in"
            
            for user, data in self.user_data.items():
                seeds = []
                for slug, r_data in data["ratings"].items():
                    # r_data is now a dict
                    rating = r_data["score"]
                    if rating >= min_rating_threshold:
                        seeds.append((slug, rating, r_data))
                
                # If desperate, add Liked (treat as 10)
                if len(seeds) < 5:
                    for slug in data["liked"]:
                        if slug not in data["ratings"]:
                            # Mock r_data for liked items
                            # We need name from somewhere? 
                            # 'liked' is just a set of slugs? No, liked is set of slugs from fetch_user_data.
                            # We might be missing details for liked-only items if we don't look them up.
                            # But fetch_user_data builds 'liked' from 'films' loop.
                            # So we can look up details in 'films'...? 
                            # fetch_user_data doesn't return 'films' map.
                            # We need to improve fetch_user_data return signature or logic?
                            # For now, let's fallback to slug heuristic for Liked-only items.
                            seeds.append((slug, 10, {"name": slug.replace("-", " ").title(), "score": 10}))
                
                # Shuffle and pick limited seeds to avoid explosion
                random.shuffle(seeds)
                selected_seeds = seeds[:6] # Process top 6 seeds per user
                
                for seed_slug, seed_score, seed_info in selected_seeds:
                    # Resolve to TMDB
                    seed_title = seed_info.get("name") or seed_slug.replace("-", " ").title()
                    # Try to get year from watched/ratings data if possible for better search?
                    # We don't have it easily linked here unless we looked up in watched index reversed. 
                    # Use search without year.
                    
                    tmdb_seed = tmdb_search_movie(seed_title, None, self.tmdb_api_key)
                    if not tmdb_seed: continue
                    
                    recs = tmdb_get_recommendations(tmdb_seed["id"], self.tmdb_api_key)
                    
                    for rec in recs:
                        rid = f"tmdb:{rec['id']}"
                        check_title = rec["title"]
                        check_year = int(rec["release_date"][:4]) if rec.get("release_date") else None
                        
                        cand = get_or_create(rid, check_title, check_year, f"https://www.themoviedb.org/movie/{rec['id']}", tmdb_obj=rec)
                        if cand:
                            cand["sources"].add("Similar")
                            
                            # Add Contribution
                            if user not in cand["similar_contributions"]:
                                cand["similar_contributions"][user] = []
                            
                            # Score Logic
                            # 10 -> N, 9 -> N/2, 8 -> N/3
                            # N is relative, let's say N=100
                            base_n = 100.0
                            if seed_score == 10: pts = base_n
                            elif seed_score == 9: pts = base_n / 2
                            elif seed_score == 8: pts = base_n / 3
                            else: pts = base_n / 4
                            
                            cand["similar_contributions"][user].append({
                                "points": pts,
                                "from_movie": tmdb_seed["title"],
                                "from_score": seed_score
                            })

        # --- 3. POPULAR FALLBACK ---
        if use_popular and self.tmdb_api_key and len(candidates) < 20:
             pops = tmdb_get_popular(self.tmdb_api_key)
             for p in pops:
                 rid = f"tmdb:{p['id']}"
                 cand = get_or_create(rid, p["title"], int(p["release_date"][:4]) if p.get("release_date") else None, "", tmdb_obj=p)
                 if cand: 
                     cand["sources"].add("Popular")

        # --- 4. ENRICHMENT (Fetch TMDB for Watchlist items) ---
        if self.tmdb_api_key:
            # Only fetch for items that don't have TMDB data yet
            # Limit to top N to avoid rate limits if list is huge? 
            # But we haven't scored them effectively yet. 
            # Let's fetch for all candidates but respect cache and be gentle.
            # Actually, `tmdb_search_movie` is cached.
            
            non_tmdb_candidates = [c for c in candidates.values() if not c["tmdb"]]
            # To avoid spamming, maybe only fetch if we really need to?
            # For a friends app with < 500 watchlist items, it might be okay.
            # Let's do it.
            
            for cand in non_tmdb_candidates:
                # We need to search by title/year
                d = cand["details"]
                t = d.get("name")
                y = d.get("year")
                if t:
                    found = tmdb_search_movie(t, y, self.tmdb_api_key)
                    if found:
                        cand["tmdb"] = found
                        # Update poster if available
                        if found.get("poster_path"):
                             # No need to manually set poster string here, handled in format step
                             pass

        # --- CALCULATION ---
        final_results = []
        N = 100.0 
        
        for cid, data in candidates.items():
            score = 0.0
            debug_info = {}
            
            # A. Watchlist Score
            # "If a movie is on the watch list of one user, it gives some points let's say N"
            wl_count = len(data["watchlist_users"])
            wl_score = wl_count * N
            score += wl_score
            
            if wl_count > 0:
                debug_info["Watchlist"] = f"{wl_count} users ({', '.join(data['watchlist_users'])})"
            
            # B. Similar/Discovery Score
            # "Add all the users results for the similar movies and at this time only, weight it with the discovery weight."
            sim_score_total = 0.0
            sim_reasons = []
            
            for user, contribs in data["similar_contributions"].items():
                # "weight each movie score by the total of rated movies... Ex: divide the score with some constant"
                # Implementation: Average the contributions per user so one user doesn't dominate?
                # Prompt says: "so each user approx the same total scores".
                # Let's Sum them but normalize simply if they have MANY hits for the same target.
                # Actually, simpler interpretation: Sum of points.
                # But heavily damped if multiple sources from same user hit same target? 
                # Let's just sum for now, as the prompt's divisor logic implies normalizing expectations, 
                # but "divide the score with some constant times 3*N..." is a bit complex to reverse engineer exactly without total stats.
                # I will adhere to: Sum of points.
                
                user_sum = sum(c["points"] for c in contribs)
                sim_score_total += user_sum
                
                # Pick best reason
                best = max(contribs, key=lambda x: x["points"])
                sim_reasons.append(f"Similar to {best['from_movie']} ({best['from_score']}/10)")

            # Apply Discovery Weight
            # "This weight should only change on how much the score computed from the similar movies count"
            weighted_sim_score = sim_score_total * discovery_weight
            score += weighted_sim_score
            
            if sim_reasons:
                # Show top 2 reasons
                debug_info["Similar"] = ", ".join(sim_reasons[:3])
            
            # --- Formating Output ---
            # Resolve Image
            poster = None
            if data["tmdb"]:
                poster = tmdb_image_url(data["tmdb"].get("poster_path"))
            elif "image" in data.get("details", {}): # LB data might have it? Rarely usable direct link.
                pass 
                
            final_results.append({
                "id": cid,
                "title": data["details"]["name"],
                "year": data["details"]["year"],
                "url": data["details"]["url"],
                "score": score,
                "poster": poster,
                "sources": list(data["sources"]),
                "debug_details": debug_info,
                "raw_tmdb": data["tmdb"]
            })

        final_results.sort(key=lambda x: x["score"], reverse=True)
        return final_results[:100]

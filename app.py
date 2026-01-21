import streamlit as st
import os
from utils import RecommendationEngine, tmdb_image_url

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

st.set_page_config(
    page_title="Letterboxd Friends",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CSS ---
st.markdown("""
<style>
    .block-container { padding-top: 1rem; max-width: 95% !important; }
    .stButton>button { width: 100%; border-radius: 4px; font-weight: bold; }
    
    /* Card */
    .movie-card {
        background-color: #14181c; 
        border-radius: 6px;
        overflow: hidden;
        border: 1px solid #2c3440;
        margin-bottom: 0.5rem;
    }
    
    .poster-container { width: 100%; aspect-ratio: 2/3; background-color: #000; position: relative; }
    .poster-img { width: 100%; height: 100%; object-fit: cover; }
    
    .card-content { padding: 0.5rem; }
    .movie-title { color: #fff; font-size: 1rem; font-weight: 700; margin-bottom: 0.2rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .card-meta { display: flex; justify-content: space-between; font-size: 0.8rem; color: #888; margin-bottom: 0.4rem; }
    .tmdb-rating { color: #01b4e4; font-weight: bold; }
    
    /* Sources Chips */
    .source-chip {
        display: inline-block;
        font-size: 0.7rem;
        padding: 2px 6px;
        border-radius: 4px;
        margin-right: 4px;
        background: #333;
        color: #ccc;
    }
    .source-watchlist { border: 1px solid #40bcf4; color: #40bcf4; background: rgba(64, 188, 244, 0.1); }
    .source-similar { border: 1px solid #ff8000; color: #ff8000; background: rgba(255, 128, 0, 0.1); }
    
    .lb-link { color: #678; font-size: 0.75rem; text-decoration: none; display: block; margin-top: 4px; text-align: right; }
    .lb-link:hover { color: #fff; }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if "usernames" not in st.session_state:
    st.session_state.usernames = []

def add_user():
    new_user = st.session_state.new_user_input.strip()
    if new_user and new_user not in st.session_state.usernames:
        st.session_state.usernames.append(new_user)
        st.session_state.new_user_input = "" 
def remove_user(u):
    if u in st.session_state.usernames:
        st.session_state.usernames.remove(u)

# --- SIDEBAR ---
with st.sidebar:
    st.header("Discovery Settings")
    tmdb_key = os.getenv("TMDB_API", "")
    if not tmdb_key:
        st.warning("TMDB Key Missing! Discovery Limited.")
        
    st.markdown("### Sources")
    use_watchlist = st.toggle("Use Watchlists", value=True)
    use_similar = st.toggle("Similar to Your Favorites", value=True)
    use_popular = st.toggle("Trending / Popular", value=False)
    
    if use_similar:
        st.markdown("### Tuning")
        discovery_weight = st.slider("Discovery Weight", 0.0, 1.0, 0.5, help="Adjust impact of 'Similar' recommendations.")
    else:
        discovery_weight = 0.0

    st.markdown("---")
    sort_option = st.selectbox("Sort By", ["Smart Match", "TMDB Rating", "Release Year"])

# --- MAIN ---
st.title("🎬 Letterboxd Friends")

col_input, col_display = st.columns([1, 2])
with col_input:
    st.text_input("Add User", key="new_user_input", on_change=add_user, placeholder="Username + Enter")
with col_display:
    if st.session_state.usernames:
        st.write("Group:")
        cols = st.columns(len(st.session_state.usernames) + 1)
        for i, u in enumerate(st.session_state.usernames):
            if cols[i].button(f"{u} ✖", key=f"rem_{u}"):
                remove_user(u)
                st.rerun()

st.markdown("---")

if st.session_state.usernames:
    engine = RecommendationEngine(st.session_state.usernames, tmdb_api_key=tmdb_key)
    
    with st.spinner("Analyzing Taste..."):
        engine.fetch_data()
        recs = engine.generate_recommendations(
            use_watchlist=use_watchlist,
            use_similar=use_similar,
            use_popular=use_popular,
            discovery_weight=discovery_weight
        )
    
    if engine.errors:
        for e in engine.errors: st.warning(e)

    # Sorting
    if sort_option == "TMDB Rating":
        recs.sort(key=lambda x: (x.get("raw_tmdb") or {}).get("vote_average", 0), reverse=True)
    elif sort_option == "Release Year":
        recs.sort(key=lambda x: str(x.get("year", "0")), reverse=True)
    # else: Already sorted by Smart Match

    if not recs:
        st.info("No movies found.")
    else:
        st.success(f"Found {len(recs)} candidates.")
        
        # Grid Layout
        cols = st.columns(5)
        for idx, movie in enumerate(recs):
            col = cols[idx % 5]
            
            # Data Prepare
            title = movie["title"]
            year = movie.get("year", "")
            poster_url = movie.get("poster") or "https://s.ltrbxd.com/static/img/empty-poster-70.png"
            
            tmdb = movie.get("raw_tmdb") or {}
            vote = tmdb.get("vote_average")
            rating_str = f"★ {vote:.1f}" if vote else ""
            
            sources_html = ""
            for s in movie["sources"]:
                cls = "source-watchlist" if s == "Watchlist" else "source-similar" if s == "Similar" else ""
                sources_html += f'<span class="source-chip {cls}">{s}</span>'

            with col:
                # Custom HTML Card
                st.markdown(f"""
                <div class="movie-card">
                    <div class="poster-container"><img src="{poster_url}" class="poster-img"></div>
                    <div class="card-content">
                        <div class="card-meta"><span class="tmdb-rating">{rating_str}</span><span>{year}</span></div>
                        <div class="movie-title" title="{title}">{title}</div>
                        <div>{sources_html}</div>
                        <a href="{movie['url']}" target="_blank" class="lb-link">View on LB</a>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Expandable Details (The User Request)
                # Using a short label like "Why?" or "Details"
                with st.expander("Details"):
                    debug = movie.get("debug_details", {})
                    if "Watchlist" in debug:
                        st.caption(f"**In Watchlists:** {debug['Watchlist']}")
                    
                    if "Similar" in debug:
                        st.caption(f"**Similar To:** {debug['Similar']}")
                    
                    st.caption(f"**Score:** {movie['score']:.1f}")

else:
    st.info("Add users to start.")

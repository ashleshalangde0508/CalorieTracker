import hashlib, hmac, secrets, sqlite3, json
from pathlib import Path
from datetime import date, timedelta
import joblib
import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
DB = BASE / 'calories_tracker.db'
FOOD = BASE / 'food_nutrition_cleaned.csv'
MODEL = BASE / 'best_calories_burned_model.joblib'
META = BASE / 'model_metadata.json'
TARGET = 2000
MEALS = ['Breakfast', 'Lunch', 'Snack', 'Dinner']
ACTIVITY = {'Sedentary': 1.2, 'Lightly active': 1.375, 'Moderately active': 1.55, 'Very active': 1.725, 'Extra active': 1.9}
NUTRIENTS = {'Calories': 'Calories_kcal_per_100g', 'Protein': 'Protein_g_per_100g', 'Carbs': 'Carbohydrates_g_per_100g', 'Fat': 'Fat_g_per_100g', 'Fiber': 'Fiber_g_per_100g'}
st.set_page_config(page_title='Calories Tracker', page_icon='🔥', layout='wide')


def connect():
    return sqlite3.connect(DB)


def init_db():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, password TEXT)')
        c.execute('''CREATE TABLE IF NOT EXISTS meals(id INTEGER PRIMARY KEY, user_id INTEGER, day TEXT, meal TEXT,
                  food TEXT, grams REAL, calories REAL, protein REAL, carbs REAL, fat REAL, fiber REAL)''')


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200000)
    return salt.hex(), hashed.hex()


def authenticate():
    st.title('🔥 Calories Tracker')
    login, signup = st.tabs(['Log in', 'Create account'])
    with login:
        with st.form('login'):
            name = st.text_input('Username').strip().lower()
            pw = st.text_input('Password', type='password')
            submit = st.form_submit_button('Log in')
        if submit:
            with connect() as c:
                user = c.execute('SELECT id,salt,password FROM users WHERE username=?', (name,)).fetchone()
            if user and hmac.compare_digest(password_hash(pw, bytes.fromhex(user[1]))[1], user[2]):
                st.session_state.user = (user[0], name)
                st.rerun()
            st.error('Incorrect username or password.')
    with signup:
        with st.form('signup'):
            name = st.text_input('Choose username').strip().lower()
            pw = st.text_input('Choose password', type='password')
            submit = st.form_submit_button('Create account')
        if submit:
            if len(name) < 3 or len(pw) < 6:
                st.error('Username must have 3+ characters and password 6+ characters.')
            else:
                salt, hashed = password_hash(pw)
                try:
                    with connect() as c:
                        c.execute('INSERT INTO users(username,salt,password) VALUES(?,?,?)', (name, salt, hashed))
                    st.success('Account created. Please log in.')
                except sqlite3.IntegrityError:
                    st.error('Username already exists.')


def load_food():
    df = pd.read_csv(FOOD).dropna(subset=['Food_Name'])
    df['Food_Name'] = df['Food_Name'].astype(str).str.strip()
    return df.drop_duplicates('Food_Name').set_index('Food_Name')


def get_meals(uid, start, end):
    with connect() as c:
        return pd.read_sql_query('SELECT * FROM meals WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY day,id', c,
                                 params=(uid, str(start), str(end)))


def save_meals(uid, day, rows):
    """Button callback: save the rows, clear the food selection (no double save) and leave a message."""
    with connect() as c:
        c.executemany(
            'INSERT INTO meals(user_id,day,meal,food,grams,calories,protein,carbs,fat,fiber) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',  # 10 columns -> exactly 10 placeholders
            [(uid, str(day), m, f, g, n['Calories'], n['Protein'], n['Carbs'], n['Fat'], n['Fiber']) for f, g, m, n in rows])
    st.session_state.picked_foods = []
    st.session_state.flash = f'Saved {len(rows)} item(s) for {day}.'


def log_food(uid):
    st.subheader('Log what you ate')
    flash = st.session_state.pop('flash', None)
    if flash:
        st.success(flash)
    try:
        foods = load_food()
    except Exception as e:
        st.error(f'Could not load food CSV: {e}')
        return
    day = st.date_input('Date', value=date.today(), max_value=date.today())
    picked = st.multiselect('Choose foods', foods.index.tolist(), key='picked_foods')
    rows = []
    for food in picked:
        a, b, c = st.columns([3, 1, 2])
        a.write(food)
        # keys use the food name, so grams/meal stay with the right food if you add or remove items
        grams = b.number_input(f'Grams - {food}', 1, 5000, 100, key=f'g_{food}')
        meal = c.selectbox(f'Meal - {food}', MEALS, key=f'm_{food}')
        n = {k: round(float(foods.loc[food, col]) * grams / 100, 2) if col in foods and pd.notna(foods.loc[food, col]) else 0
             for k, col in NUTRIENTS.items()}
        rows.append((food, grams, meal, n))
    if rows:
        st.dataframe(pd.DataFrame([{'Food': f, 'Grams': g, 'Meal': m, **n} for f, g, m, n in rows]), hide_index=True)
        st.button('Save meals', type='primary', on_click=save_meals, args=(uid, day, rows))
    saved = get_meals(uid, day, day)
    if not saved.empty:
        st.write('Saved meals')
        st.dataframe(saved.drop(columns=['id', 'user_id']), hide_index=True)
        labels = {int(r.id): f'{r.food} - {r.grams:g} g - {r.calories:.0f} kcal' for r in saved.itertuples()}
        ids = st.multiselect('Select entries to delete', list(labels), format_func=labels.get)
        if st.button('Delete selected') and ids:
            with connect() as c:
                c.executemany('DELETE FROM meals WHERE id=? AND user_id=?', [(i, uid) for i in ids])
            st.rerun()


def weekly_report(uid):
    st.subheader('Weekly report')
    end = date.today()
    start = end - timedelta(days=6)
    df = get_meals(uid, start, end)
    if df.empty:
        st.info('No meals logged in the last 7 days yet.')
        return
    total = df['calories'].sum()
    a, b, c = st.columns(3)
    a.metric('Total calories', f'{total:.0f} kcal')
    b.metric('Average per logged day', f'{df.groupby("day").calories.sum().mean():.0f} kcal')
    c.metric('Meals logged', len(df))
    chart = df.groupby('day')['calories'].sum()
    st.line_chart(chart)
    st.dataframe(df.drop(columns=['id', 'user_id']), hide_index=True)
    st.download_button('Download report CSV', df.to_csv(index=False), 'calories_report.csv', 'text/csv')


def health_tab():
    st.subheader('BMI and daily calorie needs')
    a, b, c = st.columns(3)
    sex = a.selectbox('Sex', ['Male', 'Female'])
    age = b.number_input('Age', 10, 100, 25)
    height = c.number_input('Height (cm)', 100, 220, 170)
    weight = a.number_input('Weight (kg)', 30.0, 200.0, 70.0)
    activity = b.selectbox('Activity level', list(ACTIVITY))
    bmi = weight / (height / 100) ** 2
    ree = 10 * weight + 6.25 * height - 5 * age + (5 if sex == 'Male' else -161)
    calories = round(ree * ACTIVITY[activity])
    c.metric('BMI', f'{bmi:.1f}')
    st.metric('Estimated daily calories', f'{calories} kcal/day')
    st.caption('These are general estimates, not medical advice.')


def predictor_tab():
    st.subheader('Calories burned predictor')
    try:
        model, meta = joblib.load(MODEL), json.loads(META.read_text())
    except Exception as e:
        st.error(f'Could not load model files: {e}')
        return
    ranges = {'Age_years': (10,100,25), 'Height_cm': (100,220,170), 'Weight_kg': (30,200,70),
              'Duration_min': (1,300,30), 'Heart_Rate_bpm': (40,220,110), 'Body_Temp_C': (35.0,42.0,37.5)}
    values = {}
    cols = st.columns(3)
    for i, feat in enumerate(meta['features']):
        if feat == 'BMI':
            continue
        with cols[i % 3]:
            if feat == 'Gender':
                values[feat] = st.selectbox('Gender', ['Male', 'Female'])
            elif feat in ranges:
                lo, hi, default = ranges[feat]
                values[feat] = st.number_input(feat, min_value=lo, max_value=hi, value=default)
            else:
                values[feat] = st.number_input(feat, value=0.0)
    if 'BMI' in meta['features']:
        values['BMI'] = values['Weight_kg'] / (values['Height_cm'] / 100) ** 2
    if st.button('Predict calories burned', type='primary'):
        pred = model.predict(pd.DataFrame([values])[meta['features']])[0]
        st.success(f'Estimated calories burned: {pred:.0f} kcal')
        st.caption(f"Model: {meta['best_model']} | Test MAE: {meta['test_MAE']:.2f} kcal")


def model_tab():
    st.subheader('Model performance')
    comp = BASE / 'model_comparison.csv'
    if comp.exists():
        st.dataframe(pd.read_csv(comp), hide_index=True)
    try:
        meta = json.loads(META.read_text())
        st.write(f"**Best model:** {meta['best_model']}  |  **MAE:** {meta['test_MAE']:.2f}  |  **RMSE:** {meta['test_RMSE']:.2f}  |  **R²:** {meta['test_R2']:.3f}")
    except Exception as e:
        st.warning(f'Model metadata unavailable: {e}')


init_db()
if 'user' not in st.session_state:
    authenticate()
    st.stop()
uid, username = st.session_state.user
with st.sidebar:
    st.write(f'Logged in as **{username}**')
    if st.button('Log out'):
        del st.session_state.user
        st.rerun()
st.title('🔥 Calories Tracker')
t1, t2, t3, t4 = st.tabs(['Log My Day', 'Weekly Report', 'BMI & Daily Target', 'Calories Burned ML'])
with t1: log_food(uid)
with t2: weekly_report(uid)
with t3: health_tab()
with t4:
    predictor_tab()
    with st.expander('Model comparison'):
        model_tab()

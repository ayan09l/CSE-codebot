from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import subprocess, json, random, os, requests

app = Flask(__name__)
app.secret_key = "supersecretkey"   # change in production
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///students.db"
db = SQLAlchemy(app)

# ----------------- Database Models -----------------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    role = db.Column(db.String(10))  # "user" or "bot"
    message = db.Column(db.Text)

class QuizScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    score = db.Column(db.Integer)  # 1 = correct, 0 = incorrect

# ✅ Create tables
with app.app_context():
    db.create_all()

# ----------------- HuggingFace Fallback -----------------
HF_API_TOKEN = os.getenv("HF_API_TOKEN")   # set this in environment
HF_MODEL = "microsoft/DialoGPT-small"

def ask_huggingface(prompt):
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"} if HF_API_TOKEN else {}
    payload = {"inputs": prompt}
    try:
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{HF_MODEL}",
            headers=headers, json=payload, timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and "generated_text" in data[0]:
                return data[0]["generated_text"]
            else:
                return str(data)
        else:
            return f"⚠️ HuggingFace Error {response.status_code}"
    except Exception as e:
        return f"❌ HuggingFace error: {str(e)}"

# ----------------- Routes -----------------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/chatroom")
def chatroom():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    user = Student.query.get(user_id)
    chats = ChatHistory.query.filter_by(user_id=user_id).all()
    scores = QuizScore.query.filter_by(user_id=user_id).all()

    total_quizzes = len(scores)
    correct = sum(s.score for s in scores)
    accuracy = (correct / total_quizzes * 100) if total_quizzes > 0 else 0

    return render_template("index.html",
                           user=user,
                           chats=chats,
                           scores=scores,
                           total_quizzes=total_quizzes,
                           correct=correct,
                           accuracy=accuracy)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = Student.query.filter_by(username=username, password=password).first()
        if user:
            session["user_id"] = user.id
            return redirect(url_for("chatroom"))
        else:
            return "❌ Invalid username or password"
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if Student.query.filter_by(username=username).first():
            return "❌ Username already exists!"
        new_user = Student(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))

@app.route("/new_chat")
def new_chat():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    ChatHistory.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    return redirect(url_for("chatroom"))

# ----------------- Chatbot Logic -----------------
@app.route("/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"reply": "Please login first."})

    user_id = session["user_id"]
    user_message = request.json.get("message", "").strip()

    try:
        db.session.add(ChatHistory(user_id=user_id, role="user", message=user_message))
        db.session.commit()

        reply = ""

        # ✅ Quiz Mode
        if user_message.lower() == "quiz":
            with open("quiz_data.json", "r") as f:
                quiz_data = json.load(f)
            question = random.choice(quiz_data)
            session["quiz_question"] = question
            reply = f"📝 Quiz Time!\n{question['question']}"

        elif "quiz_question" in session:
            correct_answer = session["quiz_question"]["answer"].lower()
            if user_message.lower() == correct_answer:
                reply = "✅ Correct! 🎉 Well done!"
                db.session.add(QuizScore(user_id=user_id, score=1))
            else:
                reply = f"❌ Incorrect. The correct answer is: {session['quiz_question']['answer']}"
                db.session.add(QuizScore(user_id=user_id, score=0))
            db.session.commit()
            session.pop("quiz_question")

        # ✅ Python Code Execution
        elif user_message.lower().startswith("run:"):
            code = user_message[4:].strip()
            try:
                result = subprocess.run(
                    ["python", "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                output = result.stdout if result.stdout else result.stderr
                reply = f"💻 Python Output:\n{output}"
            except Exception as e:
                reply = f"❌ Error running code: {str(e)}"

        # ✅ Normal Chatbot
        else:
            try:
                with open("knowledge.txt", "r") as f:
                    knowledge = f.read()
            except:
                knowledge = ""

            full_prompt = f"""
You are a friendly CSE teaching assistant for college students. 
Answer clearly with examples and use the knowledge base below whenever relevant. 

Knowledge Base:
{knowledge}

Question: {user_message}
Answer:
"""
            try:
                result = subprocess.run(
                    ["ollama", "run", "llama3"],
                    input=full_prompt.encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60
                )
                reply = result.stdout.decode("utf-8").strip()
                if not reply:
                    reply = "⚠️ No response from Ollama."
            except Exception:
                # ✅ Fallback to HuggingFace
                reply = ask_huggingface(full_prompt)

        db.session.add(ChatHistory(user_id=user_id, role="bot", message=reply))
        db.session.commit()

        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"reply": f"❌ Error: {str(e)}"})

if __name__ == "__main__":
    app.run(debug=True)

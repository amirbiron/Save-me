from flask import Flask

# This creates the Flask application object that Gunicorn will run.
app = Flask('')

@app.route('/')
def health_check():
    """
    This route is essential for Render's health check to prevent the service from sleeping.
    """
    return "OK", 200
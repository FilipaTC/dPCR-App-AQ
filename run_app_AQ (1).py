import os
from shiny import run_app

def main():
    base_path = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(base_path, "app", "app_AQ.py")

    port = int(os.environ.get("PORT", 8008))  # Render injects PORT
    run_app(app_path, host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()

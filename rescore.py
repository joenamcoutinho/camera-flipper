"""Re-score everything still waiting in the queue, from the command line.

Scores are calculated once, when a listing is first found. So after a
deploy that changes resale figures, the assumed-resale setting, or how
condition phrases are read, the listings already sitting in your queue
still carry their old scores until this runs. The deploy script calls it
automatically; you can also run it by hand:

    source venv/bin/activate && python rescore.py
"""
from webapp import rescore

if __name__ == "__main__":
    result = rescore()
    print(f"Re-scored {result['rescored']} listing(s): "
          f"{result['accessories_found']} flagged as accessories, "
          f"{result['bundles_found']} as bundles.")

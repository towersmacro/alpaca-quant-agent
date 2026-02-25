import random
import json

def generate_mock_signal():
    """
    Generates a random trading signal for testing purposes.
    Returns a dictionary compatible with the expected final_state structure.
    """
    actions = ["LONG", "SHORT", "HOLD"]
    decision = random.choice(actions)
    rr_ratio = f"1:{random.randint(1, 4)}"
    confidence = f"{random.randint(50, 100)}%"
    
    decision_json = json.dumps({
        "decision": decision,
        "risk_reward_ratio": rr_ratio,
        "confidence": confidence,
        "reasoning": "Random mock signal for testing."
    })
    
    return {
        "final_trade_decision": decision_json
    }

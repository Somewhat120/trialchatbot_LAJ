# dspy_engine/signatures.py
# DSPy 3.x signatures for LAJ judge outputs (fielded outputs for direct parsing)

import dspy


class _BaseJudgeSignature(dspy.Signature):
    """
    You are an LLM-as-Judge. Follow the provided rubric exactly.

    Return ONLY a single JSON object with exactly these keys:
      - "reasoning": string
      - "final_score": integer
    """

    # Inputs
    skill_text: str = dspy.InputField(desc="Locked rubric + decision rules + fixed demonstrations (SKILL.md).")
    question: str = dspy.InputField(desc="User question.")
    answer: str = dspy.InputField(desc="Chatbot answer to score.")

    # Outputs (fielded, so DSPy can parse them directly)
    reasoning: str = dspy.OutputField(desc="Brief reasoning aligned to the rubric.")
    final_score: int = dspy.OutputField(desc="Final integer score per rubric.")


class Binary01Signature(_BaseJudgeSignature):
    """
    Scoring constraint: final_score must be either 0 or 1.
    """


class Endpoints15Signature(_BaseJudgeSignature):
    """
    Scoring constraint: final_score must be either 1 or 5.
    """


class Likert15Signature(_BaseJudgeSignature):
    """
    Scoring constraint: final_score must be an integer in {1,2,3,4,5}.
    """

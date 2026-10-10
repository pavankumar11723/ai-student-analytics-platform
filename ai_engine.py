
def generate_insight(attendance, score, study_hours, risk):

    reasons = []
    recommendations = []
    priority = "Maintain current progress"

    # ATTENDANCE
    if attendance < 60:
        reasons.append(f"low attendance ({attendance}%)")
        recommendations.append("Create an attendance improvement plan")
        priority = "URGENT: Improve attendance"

    elif attendance < 75:
        reasons.append(f"moderate attendance ({attendance}%)")
        recommendations.append("Encourage more regular class attendance")
        priority = "HIGH: Improve attendance"

    # ACADEMIC SCORE
    if score < 50:
        reasons.append(f"low academic score ({score}%)")
        recommendations.append("Provide additional academic support")

        if attendance >= 60:
            priority = "URGENT: Improve academic performance"

    elif score < 70:
        reasons.append(f"moderate academic score ({score}%)")
        recommendations.append("Provide targeted subject mentoring")

        if attendance >= 75 and priority == "Maintain current progress":
            priority = "HIGH: Improve academic performance"

    # STUDY HOURS
    if study_hours < 3:
        reasons.append(f"low study time ({study_hours} hours/day)")
        recommendations.append("Create a structured daily study schedule")

        if attendance >= 75 and score >= 50:
            priority = "HIGH: Increase daily study time"

    elif study_hours < 5:
        reasons.append(f"moderate study time ({study_hours} hours/day)")
        recommendations.append("Encourage additional self-study time")

        if (
            attendance >= 75
            and score >= 70
            and priority == "Maintain current progress"
        ):
            priority = "MEDIUM: Increase self-study time"

    # DEFAULT MESSAGE
    if not reasons:
        reasons.append("student performance indicators are currently stable")
        recommendations.append("Continue regular academic monitoring")

    # EXPLANATION
    explanation = (
        f"Student is classified as {risk} risk because of "
        + ", ".join(reasons)
        + "."
    )

    # RECOMMENDATION
    recommendation = ". ".join(recommendations) + "."

    # RETURN AI INSIGHT
    return {
        "risk": risk,
        "explanation": explanation,
        "recommendation": recommendation,
        "priority": priority
    }


# TEST THE AI ENGINE
if __name__ == "__main__":
    result = generate_insight(62, 58, 3, "High")
    print(result)
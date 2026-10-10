
 // ========================================
 // AI STUDENT ANALYTICS PLATFORM
 // ========================================

console.log("AI Student Analytics JavaScript connected!");


// ========================================
// LOAD DASHBOARD STATISTICS
// ========================================

async function loadDashboard() {
    const aiInsight = document.getElementById("aiInsight");

    try {
        const response = await fetch("/api/dashboard");

        if (!response.ok) {
            throw new Error(`Dashboard API failed: ${response.status}`);
        }

        const data = await response.json();
        console.log("Dashboard API response:", data);

        const totalStudents = document.getElementById("totalStudents");
        if (totalStudents) {
            totalStudents.textContent = data.total_students ?? 0;
        }

        const atRisk = document.getElementById("atRisk");
        if (atRisk) {
            atRisk.textContent = data.at_risk ?? 0;
        }

        const improving = document.getElementById("improving");
        if (improving) {
            improving.textContent = data.improving ?? 0;
        }

        const successRate = document.getElementById("successRate");
        if (successRate) {
            successRate.textContent = `${data.success_rate ?? 0}%`;
        }

        if (aiInsight) {
            const insight =
                data.ai_insight ??
                data.insight ??
                data.aiInsight ??
                data.ai_insights;

            aiInsight.textContent =
                insight !== null &&
                insight !== undefined &&
                String(insight).trim() !== ""
                    ? String(insight)
                    : "AI insight is not being returned by the dashboard API.";
        }
    } catch (error) {
        console.error("Dashboard loading error:", error);

        if (aiInsight) {
            aiInsight.textContent =
                "Unable to load AI insight. Please check the Flask server.";
        }
    }
}


// ========================================
// LOAD STUDENT DATA
// ========================================

async function loadStudents() {
    try {
        const response = await fetch("/api/students");

        if (!response.ok) {
            throw new Error(`Student API failed: ${response.status}`);
        }

        const students = await response.json();
        console.log("Student API response:", students);

        if (!Array.isArray(students)) {
            throw new Error("Student API did not return an array.");
        }

        const tableBody = document.getElementById("studentTableBody");

        if (tableBody) {
            tableBody.replaceChildren();

            if (students.length === 0) {
                const row = document.createElement("tr");
                const cell = document.createElement("td");

                cell.colSpan = 4;
                cell.textContent = "No students found.";
                cell.style.textAlign = "center";

                row.appendChild(cell);
                tableBody.appendChild(row);
            }

            students.forEach(student => {
                const row = document.createElement("tr");

                // Student name
                const nameCell = document.createElement("td");
                nameCell.textContent = student.name ?? "Unknown";

                // Attendance: use the same value as the student profile
                const attendanceCell = document.createElement("td");
                const attendance =
                    student.effective_attendance ??
                    student.attendance ??
                    0;

                attendanceCell.textContent = `${attendance}%`;

                // Score: use the same value as the student profile
                const scoreCell = document.createElement("td");
                const score = student.score ?? 0;

                scoreCell.textContent = `${score}%`;

                // Risk level
                const riskCell = document.createElement("td");
                const riskLevel =
                    student.risk_level ??
                    student.risk ??
                    "Unknown";

                const riskBadge = document.createElement("span");
                riskBadge.classList.add("risk-badge");

                const normalizedRisk = String(riskLevel).toLowerCase();

                if (normalizedRisk === "high") {
                    riskBadge.classList.add("risk-high");
                } else if (normalizedRisk === "medium") {
                    riskBadge.classList.add("risk-medium");
                } else if (normalizedRisk === "low") {
                    riskBadge.classList.add("risk-low");
                }

                riskBadge.textContent = riskLevel;
                riskCell.appendChild(riskBadge);

                row.append(
                    nameCell,
                    attendanceCell,
                    scoreCell,
                    riskCell
                );

                // Open the corresponding student profile
                if (student.id !== undefined) {
                    row.classList.add("clickable-row");
                    row.title = "View student profile";

                    row.addEventListener("click", () => {
                        window.location.href = `/student/${student.id}`;
                    });
                }

                tableBody.appendChild(row);
            });
        }

        drawPerformanceChart(students);
    } catch (error) {
        console.error("Student loading error:", error);
    }
}


// ========================================
// DRAW PERFORMANCE CHART
// ========================================

let performanceChart = null;

function drawPerformanceChart(students) {
    const canvas = document.getElementById("performanceChart");

    if (!canvas) {
        console.error('Canvas with ID "performanceChart" was not found.');
        return;
    }

    if (typeof Chart === "undefined") {
        console.error("Chart.js is not loaded. Check index.html.");
        return;
    }

    if (!Array.isArray(students) || students.length === 0) {
        if (performanceChart) {
            performanceChart.destroy();
            performanceChart = null;
        }
        return;
    }

    const labels = students.map(student => student.name ?? "Unknown");

    const scoreData = students.map(student =>
        Number(student.score) || 0
    );

    const attendanceData = students.map(student =>
        Number(student.effective_attendance ?? student.attendance) || 0
    );

    // Display study hours as a separate dataset.
    // This is scaled to 0–100 for comparison with percentages.
    const studyHoursData = students.map(student => {
        const hours = Number(student.study_hours) || 0;
        return (hours / 24) * 100;
    });

    const datasets = [
        {
            label: "Academic Marks (%)",
            data: scoreData,
            backgroundColor: "rgba(54, 162, 235, 0.75)",
            borderColor: "rgb(54, 162, 235)",
            borderWidth: 1
        },
        {
            label: "Attendance (%)",
            data: attendanceData,
            backgroundColor: "rgba(75, 192, 120, 0.75)",
            borderColor: "rgb(75, 192, 120)",
            borderWidth: 1
        },
        {
            label: "Study Hours (scaled)",
            data: studyHoursData,
            backgroundColor: "rgba(255, 159, 64, 0.75)",
            borderColor: "rgb(255, 159, 64)",
            borderWidth: 1
        }
    ];

    const hasAssignmentData = students.every(student => {
        const value = student.assignment;

        return (
            value !== null &&
            value !== undefined &&
            value !== "" &&
            Number.isFinite(Number(value))
        );
    });

    if (hasAssignmentData) {
        datasets.push({
            label: "Assignment Marks (%)",
            data: students.map(student => Number(student.assignment)),
            backgroundColor: "rgba(153, 102, 255, 0.75)",
            borderColor: "rgb(153, 102, 255)",
            borderWidth: 1
        });
    }

    if (performanceChart) {
        performanceChart.destroy();
    }

    performanceChart = new Chart(canvas, {
        type: "bar",

        data: {
            labels: labels,
            datasets: datasets
        },

        options: {
            responsive: true,
            maintainAspectRatio: false,

            interaction: {
                mode: "index",
                intersect: false
            },

            plugins: {
                legend: {
                    display: true,
                    position: "top"
                },

                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.dataset.label;
                            const student = students[context.dataIndex];

                            if (label === "Study Hours (scaled)") {
                                const hours =
                                    Number(student.study_hours) || 0;

                                return `Study Hours: ${hours} hours/day`;
                            }

                            return `${label}: ${context.raw}%`;
                        }
                    }
                }
            },

            scales: {
                y: {
                    beginAtZero: true,
                    min: 0,
                    max: 100,

                    title: {
                        display: true,
                        text: "Percentage / Scaled Value"
                    },

                    ticks: {
                        stepSize: 20
                    }
                },

                x: {
                    title: {
                        display: true,
                        text: "Students"
                    }
                }
            }
        }
    });
}


// ========================================
// INITIALIZE DASHBOARD
// ========================================

document.addEventListener("DOMContentLoaded", function() {
    loadDashboard();
    loadStudents();
});
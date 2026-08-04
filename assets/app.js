const form = document.getElementById("evaluateForm");
const submitButton = document.getElementById("submitButton");
const loadingArea = document.getElementById("loadingArea");
const errorArea = document.getElementById("errorArea");
const resultArea = document.getElementById("resultArea");
const headquartersInput = document.getElementById("headquarters_company");
const targetInput = document.getElementById("target_company");
const headquartersResult = document.getElementById("headquartersResult");
const resultTableBody = document.getElementById("resultTableBody");
const sesJudgement = document.getElementById("sesJudgement");
const aiJudgement = document.getElementById("aiJudgement");

const itemLabels = {
  fit: "適合度",
  scale: "規模感",
  continuity: "継続性",
  growth: "成長性",
  strategy: "戦略性",
  trust: "信頼性",
  info: "情報量",
  total: "合計"
};

function judgementColorClass(total) {
  if (total >= 80) return "judge-green";
  if (total >= 60) return "judge-blue";
  if (total >= 40) return "judge-yellow";
  return "judge-red";
}

function setLoading(isLoading) {
  if (isLoading) {
    loadingArea.style.display = "block";
    submitButton.disabled = true;
    headquartersInput.readOnly = true;
    targetInput.readOnly = true;
  } else {
    loadingArea.style.display = "none";
    submitButton.disabled = false;
    headquartersInput.readOnly = false;
    targetInput.readOnly = false;
  }
}

form.addEventListener("submit", async function (e) {
  e.preventDefault();

  errorArea.style.display = "none";
  errorArea.textContent = "";
  resultArea.style.display = "none";
  resultTableBody.innerHTML = "";
  headquartersResult.textContent = "";
  sesJudgement.textContent = "";
  aiJudgement.textContent = "";
  sesJudgement.className = "judgement-box";
  aiJudgement.className = "judgement-box";

  setLoading(true);

  const headquartersCompany = headquartersInput.value.trim();
  const targetCompany = targetInput.value.trim();

  try {
    const response = await fetch("/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        headquarters_company: headquartersCompany,
        target_company: targetCompany
      })
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "評価処理でエラーが発生しました。");
    }

    headquartersResult.textContent = "取引先本社所在地: " + (data.headquarters || "不明");

    const keys = ["fit", "scale", "continuity", "growth", "strategy", "trust", "info", "total"];
    keys.forEach(function (key) {
      const row = document.createElement("tr");
      const labelCell = document.createElement("td");
      labelCell.textContent = itemLabels[key];
      const sesCell = document.createElement("td");
      sesCell.textContent = data.ses ? data.ses[key] : "";
      const aiCell = document.createElement("td");
      aiCell.textContent = data.ai ? data.ai[key] : "";
      row.appendChild(labelCell);
      row.appendChild(sesCell);
      row.appendChild(aiCell);
      resultTableBody.appendChild(row);
    });

    if (data.ses) {
      sesJudgement.textContent = "SES評価: " + data.ses.judgement + "（" + data.ses.total + "点）";
      sesJudgement.classList.add(judgementColorClass(data.ses.total));
    }

    if (data.ai) {
      aiJudgement.textContent = "AI評価: " + data.ai.judgement + "（" + data.ai.total + "点）";
      aiJudgement.classList.add(judgementColorClass(data.ai.total));
    }

    resultArea.style.display = "block";

  } catch (err) {
    errorArea.textContent = err.message;
    errorArea.style.display = "block";
  } finally {
    setLoading(false);
  }
}); 

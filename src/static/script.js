document.addEventListener('DOMContentLoaded', () => {
    // --- Get DOM Elements ---
    const uploadForm = document.getElementById('upload-form');
    const resumeFileInput = document.getElementById('resume-file');
    // Get the new textarea element
    const jobDescriptionInput = document.getElementById('job-description');
    const uploadButton = document.getElementById('upload-button');
    const loadingIndicator = document.getElementById('loading');
    const analysisResultDiv = document.getElementById('analysis-result');
    const resultContent = document.getElementById('result-content');
    const errorMessageDiv = document.getElementById('error-message');
    const historySection = document.getElementById('history-section');
    const historyList = document.getElementById('history-list');
    const refreshHistoryButton = document.getElementById('refresh-history-button');

    // --- Helper Functions ---
    function showLoading() {
        loadingIndicator.classList.remove('hidden');
        uploadButton.disabled = true;
        // Update loading text
        uploadButton.textContent = 'Analyzing...';
        errorMessageDiv.classList.add('hidden');
        analysisResultDiv.classList.add('hidden');
    }

    function hideLoading() {
        loadingIndicator.classList.add('hidden');
        uploadButton.disabled = false;
        uploadButton.textContent = 'Upload & Analyze';
    }

    function displayError(message) {
        const errorMessage = typeof message === 'string' ? message : JSON.stringify(message);
        errorMessageDiv.textContent = `Error: ${errorMessage}`;
        errorMessageDiv.classList.remove('hidden');
        analysisResultDiv.classList.add('hidden');
    }

    // --- Display Function for the Latest Analysis Result ---
    function displayAnalysisResult(data) {
        resultContent.innerHTML = '';
        errorMessageDiv.classList.add('hidden');

        const analysis = data.analysis || {};
        const filename = data.original_filename || 'N/A';
        // Check if job description was used (based on backend potentially adding a flag or field)
        const jdProvided = data.job_description_provided !== undefined ? data.job_description_provided : false; // Assume backend adds this flag


        if (analysis.llm_error) {
             displayError(`Analysis partially failed: ${analysis.llm_error}`);
             // Optionally still display any partial data extracted below
        }

        // Improved score display, mentioning context if JD was used
        let scoreText = 'Not Calculated';
        if (analysis.match_score !== undefined && analysis.match_score !== null) {
            scoreText = `${analysis.match_score}%`;
            if (jdProvided || analysis.match_score_details) { // Check if score is JD-aware
                scoreText += ` (Job Description Match)`;
            } else {
                 scoreText += ` (General Analysis)`;
            }
        }

        // Build the HTML content
        let htmlContent = `
            <h3>Analysis for: ${filename}</h3>
            <p><strong>Database ID:</strong> ${data._id || 'N/A'}</p>
            <p><strong>Match Score:</strong> ${scoreText}</p> <!-- Updated score display -->
            <hr>
            <p><strong>Name:</strong> ${analysis.extracted_name || 'Not Found'}</p>
            <p><strong>Email:</strong> ${analysis.extracted_email || 'Not Found'}</p>
            <p><strong>Phone:</strong> ${analysis.extracted_phone || 'Not Found'}</p>
            <p><strong>Experience Summary:</strong></p>
            <div class="summary-block">${analysis.experience_summary || 'Not Found'}</div>
            <p><strong>Education Summary:</strong></p>
            <div class="summary-block">${analysis.education_summary || 'Not Found'}</div>
            <p><strong>Detected Skills:</strong></p>
        `;

        if (analysis.skills && Array.isArray(analysis.skills) && analysis.skills.length > 0) {
            htmlContent += '<ul class="skills-list">';
            analysis.skills.forEach(skill => {
                const safeSkill = String(skill).replace(/</g, "<").replace(/>/g, ">");
                htmlContent += `<li>${safeSkill}</li>`;
            });
            htmlContent += '</ul>';
        } else {
            htmlContent += '<p>No specific skills extracted.</p>';
        }

        // Optionally display matching keywords if provided by backend
        if (analysis.matching_keywords && Array.isArray(analysis.matching_keywords) && analysis.matching_keywords.length > 0) {
             htmlContent += `<p><strong>Keywords Matched from JD:</strong></p>`;
             htmlContent += '<ul class="skills-list" style="background-color: #e8f8f5;">'; // Slightly different style
             analysis.matching_keywords.forEach(keyword => {
                const safeKeyword = String(keyword).replace(/</g, "<").replace(/>/g, ">");
                htmlContent += `<li>${safeKeyword}</li>`;
             });
             htmlContent += '</ul>';
        }


        resultContent.innerHTML = htmlContent;
        analysisResultDiv.classList.remove('hidden');
    }

    // --- Display Function for the Analysis History ---
    function displayHistory(resumes) {
        historyList.innerHTML = '';

        if (!resumes || !Array.isArray(resumes) || resumes.length === 0) {
            historyList.innerHTML = '<p>No analysis history found.</p>';
            return;
        }

        resumes.forEach(resume => {
            const item = document.createElement('div');
            item.classList.add('history-item');

            const analysis = resume.analysis || {};
            const jdProvided = resume.job_description_provided !== undefined ? resume.job_description_provided : false; // Check if JD was used for this entry

            // Format timestamp
            let formattedTimestamp = 'N/A';
            if (resume.upload_timestamp) {
                try {
                     const date = new Date(resume.upload_timestamp);
                     if (!isNaN(date.getTime())) { formattedTimestamp = date.toLocaleString(); }
                     else { formattedTimestamp = resume.upload_timestamp; }
                } catch (e) { formattedTimestamp = String(resume.upload_timestamp); }
            }

            // Format score for history
            let scoreText = 'N/A';
             if (analysis.match_score !== undefined && analysis.match_score !== null) {
                scoreText = `${analysis.match_score}%`;
                if (jdProvided || analysis.match_score_details) { // Check if score is JD-aware
                    scoreText += ` (JD Match)`;
                }
             }

            // Skills list
            let skillsHtml = '<p>No specific skills extracted.</p>';
            if (analysis.skills && Array.isArray(analysis.skills) && analysis.skills.length > 0) {
                skillsHtml = '<ul class="skills-list">';
                analysis.skills.forEach(skill => {
                    const safeSkill = String(skill).replace(/</g, "<").replace(/>/g, ">");
                    skillsHtml += `<li>${safeSkill}</li>`;
                });
                skillsHtml += '</ul>';
            }

            // Error notice
            const errorNotice = analysis.llm_error ? `<p style="color: red;"><small><em>Note: ${analysis.llm_error}</em></small></p>` : '';

            // Construct inner HTML
            item.innerHTML = `
                <h3>${resume.original_filename || 'Unknown Filename'}</h3>
                <span class="timestamp">Analyzed on: ${formattedTimestamp}</span>
                <p><strong>Name:</strong> ${analysis.extracted_name || 'N/A'}</p>
                <p><strong>Match Score:</strong> ${scoreText}</p> <!-- Updated score display -->
                <p><strong>Skills:</strong></p>
                ${skillsHtml}
                ${errorNotice}
                <p><small><em>DB ID: ${resume._id || 'N/A'}</em></small></p>
            `;
            historyList.appendChild(item);
        });
    }

    // --- Event Listeners ---

    // Handle Form Submission (Upload)
    uploadForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const file = resumeFileInput.files[0];
        if (!file) {
            displayError("Please select a resume file to upload.");
            return;
        }

        // Get job description text (it's okay if it's empty)
        const jobDescriptionText = jobDescriptionInput.value.trim();

        const allowedExtensions = ['.pdf', '.docx', '.txt'];
        const fileExtension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
        if (!allowedExtensions.includes(fileExtension)) {
             displayError(`Invalid file type. Allowed types: ${allowedExtensions.join(', ')}`);
             return;
        }

        showLoading();
        const formData = new FormData();
        formData.append('resume', file);
        // Append job description text ONLY IF it's not empty
        if (jobDescriptionText) {
             formData.append('job_description', jobDescriptionText);
             console.log("Appending Job Description to form data."); // Debug log
        }


        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData,
            });

            hideLoading();
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || `Upload failed with status: ${response.status}`);
            }

            console.log("Analysis successful:", data);
            displayAnalysisResult(data); // Display results
            fetchHistory(); // Refresh history

            resumeFileInput.value = ''; // Clear file input
            // Optionally clear JD input too, or leave it for next upload
            // jobDescriptionInput.value = '';

        } catch (error) {
            hideLoading();
            console.error("Upload or processing failed:", error);
            displayError(error.message || "An unexpected error occurred during upload or analysis.");
        }
    });

    // Fetch and Display History Function
    async function fetchHistory() {
        historyList.innerHTML = '<p>Loading history...</p>';
        errorMessageDiv.classList.add('hidden');

        try {
            const response = await fetch('/resumes');
            const resumes = await response.json();

            if (!response.ok) {
                throw new Error(resumes.error || `Failed to fetch history: ${response.status}`);
            }

            console.log("History data fetched:", resumes);
            displayHistory(resumes);

        } catch (error) {
            console.error("Failed to fetch history:", error);
            historyList.innerHTML = `<p class="error" style="text-align: center; color: red;">Failed to load history: ${error.message}</p>`;
        }
    }

    refreshHistoryButton.addEventListener('click', fetchHistory);

    // --- Initial Load ---
    fetchHistory();

}); // End DOMContentLoaded
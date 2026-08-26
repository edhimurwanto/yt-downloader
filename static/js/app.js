const urlInput = document.getElementById("url");

const fetchBtn = document.getElementById("fetchBtn");

const videoCard = document.getElementById("videoCard");

const thumbnail = document.getElementById("thumbnail");

const videoTitle = document.getElementById("videoTitle");

const channel = document.getElementById("channel");

const duration = document.getElementById("duration");

const errorBox = document.getElementById("error");

const mediaType = document.getElementById("mediaType");

const progressBar =
    document.getElementById("progressBar");

const progressPercentage =
    document.getElementById("progressPercentage");

const downloadSpeed =
    document.getElementById("downloadSpeed");

const downloadEta =
    document.getElementById("downloadEta");

const qualityContainer =
    document.getElementById("qualityContainer");

const loading =
    document.getElementById("loading");

const loadingText =
    document.getElementById("loadingText");

const downloadBtn =
    document.getElementById("downloadBtn");

const result =
    document.getElementById("result");

const downloadLink =
    document.getElementById("downloadLink");


// =========================================================
// Helpers
// =========================================================

function showError(message) {

    errorBox.textContent = message;

    errorBox.classList.remove("hidden");
}


function hideError() {

    errorBox.classList.add("hidden");

    errorBox.textContent = "";
}


function setLoading(isLoading, text = "Loading...") {

    if (isLoading) {

        loading.classList.remove("hidden");

        loadingText.textContent = text;

        downloadBtn.disabled = true;

        downloadBtn.classList.add(
            "opacity-50",
            "cursor-not-allowed"
        );

    } else {

        loading.classList.add("hidden");

        downloadBtn.disabled = false;

        downloadBtn.classList.remove(
            "opacity-50",
            "cursor-not-allowed"
        );
    }
}


// =========================================================
// Get Video Info
// =========================================================

async function fetchVideoInfo() {

    const url = urlInput.value.trim();

    hideError();

    result.classList.add("hidden");


    if (!url) {

        showError(
            "Please enter a YouTube URL."
        );

        return;
    }


    fetchBtn.disabled = true;

    fetchBtn.textContent = "Loading...";


    try {

        const response = await fetch(
            "/api/info",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    url: url
                })
            }
        );


        const data = await response.json();


        if (!response.ok || !data.success) {

            throw new Error(
                data.error || "Failed to get video information."
            );
        }


        const video = data.video;


        thumbnail.src = video.thumbnail;

        videoTitle.textContent =
            video.title || "Unknown title";

        channel.textContent =
            video.channel || "Unknown channel";

        duration.textContent =
            `Duration: ${video.duration_formatted || "Unknown"}`;


        videoCard.classList.remove("hidden");


        videoCard.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });


    } catch (error) {

        showError(error.message);

        videoCard.classList.add("hidden");

    } finally {

        fetchBtn.disabled = false;

        fetchBtn.textContent = "Get Video";
    }
}


// =========================================================
// Toggle Quality
// =========================================================

function toggleQuality() {

    if (mediaType.value === "audio") {

        qualityContainer.classList.add(
            "opacity-50"
        );

    } else {

        qualityContainer.classList.remove(
            "opacity-50"
        );
    }
}


// =========================================================
// Download
// =========================================================

async function downloadVideo() {

    const url = urlInput.value.trim();

    const type = mediaType.value;

    const quality =
        document.getElementById("quality").value;


    hideError();

    result.classList.add("hidden");


    if (!url) {

        showError(
            "Please enter a YouTube URL."
        );

        return;
    }


    setDownloadUI(true);


    try {

        const response = await fetch(
            "/api/download",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    url: url,
                    type: type,
                    quality: quality
                })
            }
        );


        const data = await response.json();


        if (!response.ok || !data.success) {

            throw new Error(
                data.error || "Failed to start download."
            );
        }


        await monitorDownload(
            data.download_id
        );


    } catch (error) {

        setDownloadUI(false);

        showError(error.message);
    }
}

async function monitorDownload(downloadId) {

    return new Promise((resolve, reject) => {

        const interval = setInterval(
            async () => {

                try {

                    const response = await fetch(
                        `/api/download/${downloadId}/status`
                    );


                    const data =
                        await response.json();


                    if (!response.ok || !data.success) {

                        clearInterval(interval);

                        reject(
                            new Error(
                                data.error ||
                                "Failed to get download status."
                            )
                        );

                        return;
                    }


                    const download =
                        data.download;


                    updateProgress(download);


                    // --------------------------------
                    // Completed
                    // --------------------------------

                    if (
                        download.status ===
                        "completed"
                    ) {

                        clearInterval(interval);

                        setDownloadUI(false);

                        showDownloadResult(
                            download
                        );

                        resolve();

                        return;
                    }


                    // --------------------------------
                    // Error
                    // --------------------------------

                    if (
                        download.status ===
                        "error"
                    ) {

                        clearInterval(interval);

                        setDownloadUI(false);

                        reject(
                            new Error(
                                download.error ||
                                "Download failed."
                            )
                        );

                    }

                } catch (error) {

                    clearInterval(interval);

                    setDownloadUI(false);

                    reject(error);
                }

            },

            500
        );

    });
}

function updateProgress(download) {

    const progress =
        Number(download.progress || 0);


    progressBar.style.width =
        `${progress}%`;


    progressPercentage.textContent =
        `${progress.toFixed(1)}%`;


    if (download.status === "starting") {

        loadingText.textContent =
            "Preparing download...";

    } else if (
        download.status === "downloading"
    ) {

        loadingText.textContent =
            "Downloading...";

    } else if (
        download.status === "processing"
    ) {

        loadingText.textContent =
            "Processing file...";

    }


    if (download.speed) {

        downloadSpeed.textContent =
            formatBytes(download.speed) +
            "/s";

    } else {

        downloadSpeed.textContent = "--";
    }


    if (download.eta) {

        downloadEta.textContent =
            `ETA ${formatTime(download.eta)}`;

    } else {

        downloadEta.textContent = "--";
    }
}

function formatBytes(bytes) {

    if (!bytes || bytes <= 0) {
        return "--";
    }


    const units = [
        "B",
        "KB",
        "MB",
        "GB"
    ];


    const index = Math.floor(
        Math.log(bytes) /
        Math.log(1024)
    );


    const value =
        bytes /
        Math.pow(1024, index);


    return `${value.toFixed(1)} ${units[index]}`;
}

function formatTime(seconds) {

    seconds = Math.max(
        0,
        Number(seconds)
    );


    const minutes =
        Math.floor(seconds / 60);

    const remainingSeconds =
        Math.floor(seconds % 60);


    return `${minutes}:${String(
        remainingSeconds
    ).padStart(2, "0")}`;
}

function setDownloadUI(isDownloading) {

    if (isDownloading) {

        loading.classList.remove(
            "hidden"
        );


        downloadBtn.disabled = true;

        downloadBtn.classList.add(
            "opacity-50",
            "cursor-not-allowed"
        );


        progressBar.style.width = "0%";

        progressPercentage.textContent =
            "0%";

        downloadSpeed.textContent =
            "--";

        downloadEta.textContent =
            "--";

        loadingText.textContent =
            "Preparing download...";

    } else {

        loading.classList.add(
            "hidden"
        );


        downloadBtn.disabled = false;

        downloadBtn.classList.remove(
            "opacity-50",
            "cursor-not-allowed"
        );
    }
}

function showDownloadResult(download) {

    downloadLink.href =
        `/download/${encodeURIComponent(
            download.filename
        )}`;


    downloadLink.download =
        download.filename;


    result.classList.remove(
        "hidden"
    );


    result.scrollIntoView({
        behavior: "smooth",
        block: "center"
    });
}

// =========================================================
// Enter key
// =========================================================

urlInput.addEventListener(
    "keydown",
    function (event) {

        if (event.key === "Enter") {

            fetchVideoInfo();
        }

    }
);

// ============================================================
// Support / Donation Modal
// ============================================================

const supportBtn =
    document.getElementById(
        "supportBtn"
    );

const supportModal =
    document.getElementById(
        "supportModal"
    );

const closeSupportModal =
    document.getElementById(
        "closeSupportModal"
    );

const supportBackdrop =
    document.getElementById(
        "supportBackdrop"
    );


// ============================================================
// Open Modal
// ============================================================

function openSupportModal() {

    supportModal.classList.remove(
        "hidden"
    );

    // Prevent background scrolling
    document.body.classList.add(
        "overflow-hidden"
    );
}


// ============================================================
// Close Modal
// ============================================================

function closeSupportModalHandler() {

    supportModal.classList.add(
        "hidden"
    );

    // Restore background scrolling
    document.body.classList.remove(
        "overflow-hidden"
    );
}


// ============================================================
// Event Listeners
// ============================================================

supportBtn.addEventListener(
    "click",
    openSupportModal
);


closeSupportModal.addEventListener(
    "click",
    closeSupportModalHandler
);


supportBackdrop.addEventListener(
    "click",
    closeSupportModalHandler
);


// ============================================================
// Close with ESC
// ============================================================

document.addEventListener(
    "keydown",
    (event) => {

        if (
            event.key === "Escape" &&
            !supportModal.classList.contains(
                "hidden"
            )
        ) {

            closeSupportModalHandler();
        }

    }
);
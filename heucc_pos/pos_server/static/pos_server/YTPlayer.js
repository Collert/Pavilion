let player;
let UIUpdateInterval;
let albumArt;
let songAuthorDiv;
let songTitleDiv;
let playerDialog;
let videoUrlDiv;
let songProgress;
let songDuration;
let prevVidBlocked = false;

function createYouTubePlayer(id, isPlaylist) {
    if (!window.YT) { // If YT is not loaded yet
        // Load the IFrame Player API code asynchronously
        var tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        var firstScriptTag = document.getElementsByTagName('script')[0];
        firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

        let context = {
            height: '390',
            width: '640',
            events: {
                // 'onReady': onPlayerReady,
                'onStateChange': onPlayerStateChange
            }
        };
        if (!isPlaylist) {
            context["videoId"] = id
        } else {
            context["playerVars"] = {
                'loop': 1,
                'listType': 'playlist',
                'list': id // Replace with your actual playlist ID
            }
        }
        window.onYouTubeIframeAPIReady = function() {
            player = new YT.Player('YT-player', context);
        };
    } else if (player && player.loadVideoById) {
        // If player is already initialized, just load the new video
        if (!isPlaylist) {
            player.loadVideoById(id);
        } else {
            player.loadPlaylist(id)
        }
    }
}

// function onPlayerReady(event) {
//     console.log(event)
// }

function onPlayerStateChange(event) {
    if (event.data == YT.PlayerState.PAUSED 
        || 
        event.data == YT.PlayerState.ENDED
        ||
        event.data == YT.PlayerState.UNSTARTED
    ) {
        document.querySelector('#wiggle-cont').classList.add('paused');
        clearInterval(UIUpdateInterval);
    } else {
        document.querySelector('#wiggle-cont').classList.remove('paused');
        UIUpdateInterval = setInterval(updateProgressBar, 500);
    }
    if (event.data == YT.PlayerState.UNSTARTED || prevVidBlocked) {
        prevVidBlocked = false;
        setTimeout(() => {
            const videoData = player.getVideoData()
            if (videoData.errorCode) {
                prevVidBlocked = true;
                player.nextVideo()
            } else {
                albumArt.src = `https://img.youtube.com/vi/${videoData.video_id}/default.jpg`
                songTitleDiv.textContent = videoData.title;
                songAuthorDiv.textContent = videoData.author.replace(" - Topic", "");
                songDuration.innerText = formatSeconds(parseInt(player.getDuration()));
            }
        }, 1000);
    }
}

function updateProgressBar() {
    var currentTime = player.getCurrentTime();
    var duration = player.getDuration();
    // Calculate percentage and update progress bar width
    var percentage = (currentTime / duration) * 100;
    document.documentElement.style.setProperty('--song-progress', percentage + '%');
    songProgress.innerText = formatSeconds(parseInt(currentTime));
}

function getYouTubeId(url) {
    try {
        const urlObj = new URL(url);
        let id, isPlaylist;

        // Check if the URL is for a YouTube video
        if (urlObj.hostname === "www.youtube.com" || urlObj.hostname === "youtube.com") {
            if (urlObj.searchParams.has("v")) {
                id = urlObj.searchParams.get("v");
                isPlaylist = false;
            } else if (urlObj.searchParams.has("list")) {
                id = urlObj.searchParams.get("list");
                isPlaylist = true;
            }
        }
        // Check if the URL is for a shortened YouTube video
        else if (urlObj.hostname === "youtu.be") {
            id = urlObj.pathname.substring(1);
            isPlaylist = false;
        }
        // Check if the URL is for a YouTube Music video or playlist
        else if (urlObj.hostname === "music.youtube.com") {
            if (urlObj.searchParams.has("v")) {
                id = urlObj.searchParams.get("v");
                isPlaylist = false;
            } else if (urlObj.searchParams.has("list")) {
                id = urlObj.searchParams.get("list");
                isPlaylist = true;
            }
        }
        
        // Return the ID and type if an ID was found
        if (id) {
            return [id, isPlaylist];
        }
    } catch (e) {
        console.error("Error parsing URL", e);
    }
    // Return null or some default values if no ID was found or there was an error
    return [null, false];
}

function formatSeconds(seconds) {
    let minutes = Math.floor(seconds / 60); // Get whole minutes
    let remainingSeconds = seconds % 60; // Get remaining seconds

    // Pad the minutes and seconds with leading zeros if needed
    let formattedMinutes = minutes.toString().padStart(2, '0');
    let formattedSeconds = remainingSeconds.toString().padStart(2, '0');

    return `${formattedMinutes}:${formattedSeconds}`;
}


document.addEventListener("keyup", e => {
    if (e.key === "1") {
        videoUrlDiv.value = '';
        if (!playerDialog.open) {
            playerDialog.showModal()
        } else {
            playerDialog.close()
        }
    }
})

document.addEventListener("DOMContentLoaded", () => {
    albumArt = document.querySelector("#player-art > img");
    songAuthorDiv = document.querySelector("#song-author");
    songTitleDiv = document.querySelector("#song-title");
    playerDialog = document.querySelector("dialog");
    videoUrlDiv = document.querySelector("#youtubeLinkInput");
    songProgress = document.querySelector("#cur-time");
    songDuration = document.querySelector("#total-time");

    document.getElementById('loadVideo').addEventListener('click', () => {
        const videoUrl = videoUrlDiv.value;
        const [id, isPlaylist] = getYouTubeId(videoUrl);
        if (id) {
            createYouTubePlayer(id, isPlaylist);
        } else {
            console.error("Invalid YouTube link.");
        }
    });
})
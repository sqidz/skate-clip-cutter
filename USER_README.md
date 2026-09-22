# Skate Clip Cutter

Skate Clip Cutter turns rolling session footage into usable clips by cutting out the stretches you are not in frame. Leave the camera running, pick your files, name the session, and it processes the clips for you.

## How to run (download)

1. Get the latest zip from GitHub Releases (Check for updates in the app opens this page).
2. Unzip it. If Windows blocks the folder: right-click the zip or folder -> Properties -> Unblock -> Apply.
3. Double-click **Skate Clip Cutter.bat**.
4. **Select videos** (.mp4 / .mov / .m4v).
5. Enter a **Session name** (this becomes the folder name under your output folder).
6. Choose an **Output folder** (Browse). Default is your Videos folder.
7. Click **Start**. Progress shows the current file, clips found, and a rough whole-selection ETA.
8. When it finishes, choose **Open file location** to jump to the session folder.

You do not need to install Python. The zip includes Python, FFmpeg, and the detection model.

## Tripod fence

Tripod / rock-steady only. Handheld continuous roll is out of claims. Expect false splits and shake noise if you film handheld.

Detection keeps windows where a person is in frame (YOLO). Rest-in-frame standing still can still become a clip.

## Updates

Use **Check for updates** in the app. It compares your version to the latest GitHub Release and can open the download page. It does **not** auto-patch. Download the newer zip and unzip it next to this folder.

## Tip jar

If this saves you edit time: https://buymeacoffee.com/sqidz

## Help button

In the app, **Help** opens a short popup (how to run, tripod fence, tip jar). This file is the longer guide.

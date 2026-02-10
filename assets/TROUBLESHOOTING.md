# Troubleshooting: Pushing Images to GitHub

## Common Issue: "fatal: not a git repository"

### Problem

You see this error when trying to add images:
```
PS C:\DESA\projects> git add assets/*.png assets/*.gif
fatal: not a git repository (or any of the parent directories): .git
```

### Why This Happens

Git commands only work **inside** a git repository directory. You've created your `assets` folder outside the repository.

### Your Current Situation

Based on your directory structure:
```
C:\DESA\projects\
├── assets\               ← Your images are HERE (wrong location!)
│   ├── Sauron-Outage.png
│   ├── Sauron-Real-Scan.png
│   └── Sauron-Reset-View.gif
└── sauronx.git\          ← Git repository is HERE
    └── assets\           ← Images should go HERE
```

## Solution: Move Images to the Correct Location

### Step 1: Navigate to Your Git Repository

```powershell
cd C:\DESA\projects\sauronx.git
```

### Step 2: Verify You're in the Right Place

```powershell
git status
```

You should see something like:
```
On branch main
nothing to commit, working tree clean
```

If you see "fatal: not a git repository", you're still in the wrong folder.

### Step 3: Copy Images to the Correct Assets Folder

```powershell
# Copy files from parent directory to the repo's assets folder
copy ..\assets\*.png assets\
copy ..\assets\*.gif assets\
```

Or move them if you don't need the originals:
```powershell
move ..\assets\*.png assets\
move ..\assets\*.gif assets\
```

### Step 4: Rename Your Images (Important!)

The README expects specific filenames. Rename your images:

```powershell
cd assets

# Rename to match the documentation
ren Sauron-Outage.png offline-panel.png
ren Sauron-Real-Scan.png dashboard-overview.png
ren Sauron-Reset-View.gif demo.gif

cd ..
```

Or use descriptive names that make sense and update the README accordingly.

### Step 5: ⚠️ IMPORTANT - Optimize Your GIF!

Your `Sauron-Reset-View.gif` is **23MB** which is:
- ❌ Too large for GitHub (10MB recommended limit)
- ❌ Too large for smooth web loading
- ❌ Too large for professional documentation

**You MUST optimize it before committing!**

#### Quick GIF Optimization (Windows)

**Option A: Use Online Tool (Easiest)**
1. Go to https://ezgif.com/optimize
2. Upload your `demo.gif`
3. Use these settings:
   - Compression level: 35-50
   - Optimize transparency: Yes
   - Remove duplicate frames: Yes
4. Download the optimized version
5. Replace your original file

**Option B: Use ScreenToGif (Recommended)**
1. If you used ScreenToGif to create it, reopen the project
2. Go to Editor → Statistics to see frame count
3. Reduce to 10-15 fps (Delete every other frame)
4. Reduce colors to 128 or 256
5. Lower resolution if needed (1280x720 is fine)
6. Re-export

**Option C: Use FFmpeg (Advanced)**
```powershell
# Install: winget install FFmpeg
ffmpeg -i demo.gif -vf "fps=10,scale=1280:-1:flags=lanczos" -loop 0 demo-optimized.gif
```

**Target: Get it under 5MB (ideally under 3MB)**

### Step 6: Verify File Sizes

```powershell
dir assets
```

Check that:
- ✅ PNG files are under 2MB each
- ✅ GIF file is under 5MB
- ✅ Files have correct names

### Step 7: Add Files to Git

```powershell
git add assets/*.png assets/*.gif
```

### Step 8: Commit Your Changes

```powershell
git commit -m "Add dashboard screenshots and demo GIF"
```

### Step 9: Push to GitHub

```powershell
git push
```

Or if you need to specify the branch:
```powershell
git push origin main
```

## What If My Filenames Are Different?

If you want to keep your original filenames (`Sauron-Outage.png`, etc.), you need to update the README:

### Option 1: Use Standard Names (Recommended)

Rename your files to match the documentation:
- `dashboard-overview.png` - Full dashboard view
- `offline-panel.png` - Offline panel view
- `demo.gif` - Animated demo

### Option 2: Update README to Match Your Names

Edit `README.md` and change the image references (lines 8, 12, 16):

```markdown
### Dashboard Overview
![Dashboard Overview](assets/Sauron-Real-Scan.png)

### Offline Sites Panel
![Offline Panel](assets/Sauron-Outage.png)

### Live Demo
![Demo Animation](assets/Sauron-Reset-View.gif)
```

## Complete Windows Command Sequence

Here's the full sequence from your current location:

```powershell
# 1. Navigate to git repository
cd C:\DESA\projects\sauronx.git

# 2. Verify you're in the right place
git status

# 3. Copy images to assets folder
copy ..\assets\Sauron-Real-Scan.png assets\dashboard-overview.png
copy ..\assets\Sauron-Outage.png assets\offline-panel.png

# 4. ⚠️ OPTIMIZE THE GIF FIRST! (see above)
# Then copy it:
copy ..\assets\Sauron-Reset-View-OPTIMIZED.gif assets\demo.gif

# 5. Verify file sizes
dir assets

# 6. Add to git
git add assets\*.png assets\*.gif

# 7. Commit
git commit -m "Add dashboard screenshots and optimized demo GIF"

# 8. Push
git push
```

## Troubleshooting

### Error: "failed to push some refs"

You may need to pull first:
```powershell
git pull origin main
git push origin main
```

### Error: "file exceeds GitHub's 100MB limit"

Your GIF is too large. You MUST optimize it (see Step 5).

### Images Don't Appear on GitHub

1. Check file names match exactly (case-sensitive!)
2. Verify files are in `assets/` folder
3. Check README references the correct paths
4. Wait a few minutes for GitHub to process

## Quick Reference

**Repository Location:** `C:\DESA\projects\sauronx.git`

**Expected Files:**
- `assets/dashboard-overview.png` (< 2MB)
- `assets/offline-panel.png` (< 2MB)
- `assets/demo.gif` (< 5MB)

**Git Commands:**
```powershell
cd C:\DESA\projects\sauronx.git    # Navigate to repo
git status                          # Check status
git add assets\*.png assets\*.gif  # Stage files
git commit -m "Add images"          # Commit
git push                            # Push to GitHub
```

---

**Need more help?** Check `assets/CAPTURE_GUIDE.md` for image specifications.

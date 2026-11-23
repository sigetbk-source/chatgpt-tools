function captionPlacementProtocol() { return 3; }

function captionPlacementTarget(data) {
    if (!data || data.sourceTimeVerified !== true || typeof data.expectedCaptionTrackCount !== "number" ||
        !isFinite(data.expectedCaptionTrackCount) || data.expectedCaptionTrackCount < 0 ||
        Math.floor(data.expectedCaptionTrackCount) !== data.expectedCaptionTrackCount) throw Error("Invalid caption track count");
    if (!app.project || !app.project.path || String(app.project.path) !== String(data.projectPath)) throw Error("Wrong project");
    var sequence = app.project.activeSequence;
    if (!sequence || String(sequence.sequenceID) !== String(data.sequenceGuid) || String(sequence.name) !== String(data.sequenceName)) throw Error("Wrong active sequence");
    if (sequence.captionTracks && typeof sequence.captionTracks.numTracks === "number" &&
        sequence.captionTracks.numTracks !== data.expectedCaptionTrackCount) throw Error("Caption track count changed");
    return sequence;
}

function captionPrepare(data) {
    try {
        captionPlacementTarget(data);
        if (app.project.save() === false) throw Error("Project baseline save failed");
        return "OK";
    } catch (error) { return "CEP_ERROR:" + String(error); }
}

function captionPlace(data) {
    try {
        var sequence = captionPlacementTarget(data);
        var file = new File(data.srtPath);
        if (!file.exists || !/\.srt$/i.test(data.srtPath)) throw Error("SRT missing");
        // The caller has already copied and verified a saved project backup.
        var root = app.project.rootItem;
        function matches(item, path, found) {
            if (!item) return;
            try { if (item.getMediaPath && String(new File(item.getMediaPath()).fsName) === String(new File(path).fsName)) found.push(item); } catch (_) {}
            if (item.children) for (var i = 0; i < item.children.numItems; i++) matches(item.children[i], path, found);
        }
        var found = [];
        matches(root, String(data.srtPath), found);
        if (found.length !== 1) throw Error("SRT project item missing or ambiguous");
        if (!sequence.createCaptionTrack(found[0], 0, Sequence.CAPTION_FORMAT_SUBTITLE)) throw Error("Caption track creation failed");
        var saved = app.project.save();
        if (saved === false) throw Error("Project save failed");
        return "OK";
    } catch (error) {
        return "CEP_ERROR:" + String(error);
    }
}

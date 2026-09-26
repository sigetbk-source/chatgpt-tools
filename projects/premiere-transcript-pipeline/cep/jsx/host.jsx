function captionPrepare(json) {
    try {
        var data = JSON.parse(json);
        if (!data || data.sourceTimeVerified !== true || data.expectedCaptionTrackCount !== 0) throw Error("Source-time and empty-track check required");
        if (!app.project || !app.project.path || String(app.project.path) !== String(data.projectPath)) throw Error("Wrong project");
        var sequence = app.project.activeSequence;
        if (!sequence || String(sequence.sequenceID) !== String(data.sequenceGuid) || String(sequence.name) !== String(data.sequenceName)) throw Error("Wrong active sequence");
        if (sequence.captionTracks && sequence.captionTracks.numTracks !== 0) throw Error("Caption track already exists");
        if (app.project.save() === false) throw Error("Project baseline save failed");
        return JSON.stringify({ok:true,ready:true});
    } catch (error) { return JSON.stringify({ok:false,error:String(error)}); }
}

function captionPlace(json) {
    try {
        var data = JSON.parse(json);
        if (!data || data.sourceTimeVerified !== true || data.expectedCaptionTrackCount !== 0) throw Error("Source-time and empty-track check required");
        if (!app.project || !app.project.path || String(app.project.path) !== String(data.projectPath)) throw Error("Wrong project");
        var sequence = app.project.activeSequence;
        if (!sequence || String(sequence.sequenceID) !== String(data.sequenceGuid) || String(sequence.name) !== String(data.sequenceName)) throw Error("Wrong active sequence");
        var file = new File(data.srtPath);
        if (!file.exists || !/\.srt$/i.test(data.srtPath)) throw Error("SRT missing");
        if (sequence.captionTracks && sequence.captionTracks.numTracks !== 0) throw Error("Caption track already exists");
        // The caller has already copied and verified a saved project backup.
        var root = app.project.rootItem;
        function matches(item, path, found) {
            if (!item) return;
            try { if (item.getMediaPath && String(item.getMediaPath()) === path) found.push(item); } catch (_) {}
            if (item.children) for (var i = 0; i < item.children.numItems; i++) matches(item.children[i], path, found);
        }
        var found = [];
        matches(root, String(data.srtPath), found);
        if (found.length !== 1) throw Error("SRT project item missing or ambiguous");
        if (!sequence.createCaptionTrack(found[0], 0, Sequence.CAPTION_FORMAT_SUBTITLE)) throw Error("Caption track creation failed");
        var saved = app.project.save();
        if (saved === false) throw Error("Project save failed");
        return JSON.stringify({ok:true,created:true,saved:true,projectPath:String(app.project.path),sequenceGuid:String(sequence.sequenceID)});
    } catch (error) {
        return JSON.stringify({ok:false,error:String(error)});
    }
}

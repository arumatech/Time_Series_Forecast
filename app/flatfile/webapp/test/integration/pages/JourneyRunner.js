sap.ui.define([
    "sap/fe/test/JourneyRunner",
	"flatfile/test/integration/pages/FLATFILEList.gen",
	"flatfile/test/integration/pages/FLATFILEObjectPage.gen"
], function (JourneyRunner, FLATFILEListGenerated, FLATFILEObjectPageGenerated) {
    'use strict';

    const runner = new JourneyRunner({
        launchUrl: sap.ui.require.toUrl('flatfile') + '/test/flp.html#app-preview',
        pages: {
			onTheFLATFILEListGenerated: FLATFILEListGenerated,
			onTheFLATFILEObjectPageGenerated: FLATFILEObjectPageGenerated
        },
        async: true
    });

    return runner;
});


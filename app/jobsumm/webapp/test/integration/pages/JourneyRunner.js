sap.ui.define([
    "sap/fe/test/JourneyRunner",
	"jobsumm/test/integration/pages/JOBSUMMList.gen",
	"jobsumm/test/integration/pages/JOBSUMMObjectPage.gen"
], function (JourneyRunner, JOBSUMMListGenerated, JOBSUMMObjectPageGenerated) {
    'use strict';

    const runner = new JourneyRunner({
        launchUrl: sap.ui.require.toUrl('jobsumm') + '/test/flp.html#app-preview',
        pages: {
			onTheJOBSUMMListGenerated: JOBSUMMListGenerated,
			onTheJOBSUMMObjectPageGenerated: JOBSUMMObjectPageGenerated
        },
        async: true
    });

    return runner;
});


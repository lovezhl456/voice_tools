const {defineConfig} = require('../browser/node_modules/@playwright/test');
const path = require('node:path');
if (!process.env.VT_DETECT_URL || !process.env.VT_DETECT_OUTPUT) throw Error('Use scripts/check_detection.py');
module.exports = defineConfig({
  testDir:__dirname, testMatch:'*.spec.cjs', fullyParallel:false, workers:1, retries:0, forbidOnly:true, timeout:30000,
  expect:{timeout:7000}, outputDir:path.join(process.env.VT_DETECT_OUTPUT,'test-results'),
  reporter:[['line'],['json',{outputFile:path.join(process.env.VT_DETECT_OUTPUT,'browser-results.json')}]],
  use:{baseURL:process.env.VT_DETECT_URL,browserName:'chromium',headless:true,trace:'retain-on-failure',screenshot:'only-on-failure',launchOptions:{args:['--mute-audio']}},
  projects:[{name:'desktop',use:{viewport:{width:1440,height:1080}}},
            {name:'mobile',use:{viewport:{width:390,height:844},isMobile:true,hasTouch:true}}]
});

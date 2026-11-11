const updates=require('./lib/updates');
App({onLaunch(){updates.start();},onShow(){updates.foreground();},onHide(){updates.background();}});

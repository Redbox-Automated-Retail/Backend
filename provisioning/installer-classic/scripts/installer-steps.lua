--
-- installer-steps.lua
-- Copyright (C) 2009 Redbox Automated Retail, LLC.
-- All Rights Reserved
--
-- installer-classic fork, for Windows 10/11 and a self-hosted backend. Redbox's steps in Redbox's order; what
-- changed is marked "Fork:" (see provisioning/installer-classic/README.md for the whole list). Left out: the
-- commented-out XP/Windows 7 migration steps, the QA environment, the card reader list (CRList) values, AuditPol,
-- and "Remove Blink".
--
-- Settings from installer-heading.lua: Environment, TestMode, ShellMode, UpdateCardReaderFirmware,
-- SimulateCardReader.
--

local step = nil
local silent_file = "c:\\Program Files\\Redbox\\XPTo7\\silent.txt"
local silent = kernel.fileexists( silent_file )
local installers = "${REDS}\\Installers"
local profile_file = installers .. "\\kiosk-profile.json"
local reprovision = installers .. "\\rb-reprovision\\rb-reprovision.exe"
local hal_bin = "${RedboxProgramFiles}\\halservice\\bin"
local engine_key = "HKEY_LOCAL_MACHINE\\SOFTWARE\\Redbox\\REDS\\Kiosk Engine"

-- Fork: rb-reprovision (the backend's kiosk/reprovision) applies the kiosk profile: hosts entries or DNS names,
-- the backend CA, the store ID and market, the engine's endpoints, updatemgrd's Update Service URL and keys, and
-- the Update Client / Kiosk Client / Device Service settings. Services are started later (or at the reboot).
local function apply_kiosk_profile( self )
	if not kernel.fileexists( profile_file ) then
		return fail_step( self, "P001", "The kiosk profile was not found.", profile_file .. " (kiosk-profile.json next to setup.exe)" )
	end
	local retcode = run_logged( reprovision, "apply \"" .. profile_file .. "\" --no-restart", installers .. "\\rb-reprovision", 300000, "rb-reprovision.log" )
	if retcode ~= 0 then
		return fail_step( self, "P002", "rb-reprovision could not apply the kiosk profile.", "Exit code " .. tostring( retcode ) .. "; see Update Manager\\logs\\rb-reprovision.log." )
	end
	return true
end

-- Fork: replaces "Install Digicert Certificates" (Redbox's DigiCert root and intermediate). The installer start
-- call in the next step has to reach the backend, so its hosts entries and CA go in first.
step = create_step( "Apply Kiosk Profile", "Point this PC at the backend: hosts entries, certificate authority and kiosk number from the kiosk profile." )
step.onrun = function( self )
	kernel.log( "Apply the kiosk profile (hosts, CA, store ID)..." )
	return apply_kiosk_profile( self )
end

-- add
step = create_step( "Check Internet Connectivity" , "Verify the .NET framework 4.8 is installed, verify connectivity to update service, check internet connectivity, verify data file." )
step.onrun = function ( self )

	-- Fork: the original required .NET 3.5 SP1 here. Nothing needs it any more (see "Install HAL"); the setup host
	-- and the front end run on .NET Framework 4.8 (release 528040 or later).
	kernel.log( "Is the correct version of .NET installed?" )
	local release = tonumber( tostring( kernel.getregistrykey( "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full", "Release" ) ) )
	kernel.log( "...NDP v4 Full Release: " .. tostring( release ) )
	if release == nil or release < 528040 then
		kernel.notifyeventerror( self.name, "F999", "This machine does not have the correct .NET framework installed.", "Install .NET Framework 4.8." )
		if silent == false then
			kernel.notifyinfo( "Error", "This machine does not have .NET framework 4.8 installed." )
		end
		return false
	end

	kernel.resettransfers();

	kernel.stopservice( "updatemgrd$default" )

	hash = kernel.repositorygetheadrevision( "dvd-data" )

	if hash == "" then
		kernel.log( "The latest hash for the revlog of dvd-data could not be found" )
		kernel.notifyeventerror( self.name, "F999", "The latest hash for the revlog of dvd-data could not be found", "Make sure you are using a correct data file for installation." )
		if silent == false then
			kernel.notifyinfo( "Warning", "The latest hash for the revlog of dvd-data could not be found.\nMake sure you are using a correct data file for installation." );
		end
		return false
	end

	frontEndVersion = kernel.getfrontendversion()

	-- The call to update service
	kernel.log( "Hash: " .. hash )
	kernel.log( "frontEndVersion: " .. frontEndVersion )

	startRetries = 1
	repeat -- Try this up to 3 times
		results = kernel.startinstaller(hash, frontEndVersion)
		if (results and results.success == "True") then
			kernel.log("StartInstaller successfull on try #" .. startRetries)
		else
			kernel.log("StartInstaller failed on try #" .. startRetries)
		end
		startRetries = startRetries + 1
	until ( (results and results.success == "True") or startRetries > 3)

	-- Fork: also fails when there was no answer at all (the original indexed a nil result).
	if not results or results.success ~= "True" then
		kernel.log( "Could not contact Update Service." )
		kernel.notifyeventerror( self.name, "F999", "Could not contact Update Service.", "The backend's Update Service is required for installation; check the kiosk profile's address." )
		if silent == false then
			kernel.notifyinfo("Error", "Could not contact update service,\nthe backend is required for installation.\nMake sure kiosk is online.")
		end
		return false
	end

	if  results.DataFileExpiration  == "" or results.DataFileExpiration  == nil or
		results.InstallerExpiration == "" or results.InstallerExpiration == nil or
		results.CurrentDateTime     == "" or results.CurrentDateTime     == nil or
		results.URL                 == "" or results.URL                 == nil or
		results.HostEntries         == "" or results.HostEntries         == nil or
		results.guid                == "" or results.guid                == nil
	then
		kernel.log( "Update Service returned incomplete data.  Please notify the Update Service team." )
		kernel.notifyeventerror( self.name, "F100", "Update Service returned incomplete data.", "Please notify the Update Service team." )
		if silent == false then
			kernel.notifyinfo( "Error", "Update Service returned incomplete data.  Please notify the Update Service team." )
		end
		return false
	end

	kernel.log( "StartInstaller Data:" )
	kernel.log( " DataFileExpiration: " .. results.DataFileExpiration )
	kernel.log( " InstallerExpiration: " .. results.InstallerExpiration )
	kernel.log( " CurrentDateTime: " .. results.CurrentDateTime )
	kernel.log( " URL: " .. results.URL )
	kernel.log( " HostEntries: " .. results.HostEntries )

	-- The install guid is put here to later associate the machine being installed with a kiosk number
	guidpath = "C:\\Program Files\\Redbox\\REDS\\Update Manager\\installGuid.txt"
	if kernel.fileexists(guidpath) then
		guid = kernel.filereadalltext(guidpath)
	else
		kernel.filewritealltext(guidpath, results.guid)
		guid = results.guid
	end
	kernel.log(" Install Guid: " .. guid)

	if results.ErrorMessage and string.len( results.ErrorMessage ) > 0 then
		kernel.log( results.ErrorMessage )
		kernel.notifyeventerror(self.name, "F999", "There was an error.", results.ErrorMessage )
		if silent == false then
			kernel.notifyinfo( "Error", "There was an unspecified error\nduring installation.  Refer to the log\nfor more details." )
		end
		return false
	end

	-- if DataFileExpiration < 0 or is not defined, the data file expiration is not set and no action is taken
	if results.DataFileExpiration and tonumber(results.DataFileExpiration) > 1 then
		if silent == false then
			kernel.notifyinfo( "Warning", "You have " .. results.DataFileExpiration .. " days\nuntil this data file expires." )
		end
	end
	if results.DataFileExpiration and tonumber(results.DataFileExpiration) == 1 then
		if silent == false then
			kernel.notifyinfo( "Warning", "You have one more day until\nthis data file expires." )
		end
	end
	if results.DataFileExpiration and tonumber(results.DataFileExpiration) == 0 then
		if silent == false then
			kernel.notifyinfo( "Error", "This data file has expired.  Please use a newer data file." )
		end
		kernel.log( "The data file has expired." )
		kernel.notifyeventerror(self.name, "I002", "This data file has expired.", "Please use a newer data file." )
		return false
	end

	-- if InstallerExpiration < 0 or is not defined, the installer expiration is not set and no action is taken
	if results.InstallerExpiration and tonumber(results.InstallerExpiration) > 1 then
		if silent == false then
			kernel.notifyinfo( "Warning", "You have " .. results.InstallerExpiration .. " days\nuntil this installer executable expires." )
		end
	end
	if results.InstallerExpiration and tonumber(results.InstallerExpiration) == 1 then
		if silent == false then
			kernel.notifyinfo( "Warning", "You have one more day until\nthis installer executable expires." )
		end
	end
	if results.InstallerExpiration and tonumber(results.InstallerExpiration) == 0 then
		if silent == false then
			kernel.notifyinfo( "Error", "This installer has expired.  Please use a newer version of the installer." )
		end
		kernel.log( "The installer has expired." )
		kernel.notifyeventerror(self.name, "I002", "This installer has expired.", "Please use a newer version of the installer." )
		return false
	end

	-- This should always work
	if kernel.setdatetime( results.CurrentDateTime ) == false then
		if silent == false then
			kernel.notifyinfo( "Warning","There was an error setting the time,\nplease set the time manually after installation." )
		end
		kernel.log( "There was an error setting the time" )
	end

	-- The second parameter is a timeout in seconds
	if kernel.checkconnectivity(results.URL, 30) == false then
		kernel.log( "Could not connect to " ..  results.URL)
		hostsFileModifiedOnInstall = "True"
	else
		hostsFileModifiedOnInstall = "False"
	end

	kernel.log( "...We are done with prerequisites.  Sending results to update service..." )

	serialNumber = kernel.getSerialNumber()
	if (serialNumber == nil) then
		serialNumber = "none"
	end
	manufacturer = kernel.getManufacturer()
	if (manufacturer == nil) then
		manufacturer = "none"
	end

	MaxSlots = kernel.getregistrykey( "HKEY_LOCAL_MACHINE\\SOFTWARE\\Redbox\\REDS\\Kiosk Engine\\Store", "MaxSlots" )
	KioskType = "Unknown" -- default if something goes wrong
	if MaxSlots == "90" then KioskType = "R630"	end
	if MaxSlots == "72" then KioskType = "R504"	end
	data = {serial = serialNumber,
			manufacturer = manufacturer,
			hostsFile = hostsFileModifiedOnInstall,
			action = Action,
			kiosk = KioskType}

	-- Fork: kernel.finishinstaller returns a boolean; the original compared it with "True" (with the two messages
	-- swapped), so it logged "Succeeded" whatever happened.
	finishinstallersuccess = kernel.finishinstaller(guid, data) -- Reports the above information to update service
	if finishinstallersuccess == true then
		kernel.log("Finish Installer Succeeded")
	else
		kernel.log("Finish Installer Failed")
	end

	-- Dump contents to log
	kernel.log( "Installation results reported to update service:" )
	kernel.log( "Finish Installer Data:" )
	kernel.log( " serialNumber: " .. serialNumber )
	kernel.log( " manufacturer: " .. manufacturer )
	kernel.log( " hostsFileModifiedOnInstall: " .. hostsFileModifiedOnInstall )
	kernel.log( " installerAction: " .. Action )
	kernel.log( " KioskType: " .. KioskType )
	return true -- Don't stop installation just because the logging was unsuccessfull
end

--add
step = create_step( "Shutdown Legacy Services" , "Turns off legacy services including: RentalService, WatchDog, CallbackAgent, rbdservice, testmachine, memoryusage and kioskengined." )
step.onrun = function ( self )
    kernel.log( "Stopping legacy services and removing..." )
	kernel.killprocessbyname( "syncpage" )
	kernel.killprocessbyname( "kioskengine" )

	kernel.stopservice( "halsvc$default" )
	kernel.stopservice( "RentalService" )
	kernel.stopservice( "WatchDog" )
	kernel.stopservice( "CallbackAgent" )

	kernel.setservicestarttype( "WatchDog", "disabled" )
	kernel.setservicestarttype( "CallbackAgent", "disabled" )
	kernel.setservicestarttype( "RentalService", "disabled" )

	kernel.killprocessbyname( "dvd" )

	kernel.stopservice( "rbdservice" )
	kernel.stopservice( "testmachine" )
	kernel.stopservice( "memoryusage" )

	kernel.uninstallservice( "rbdservice" )
	kernel.uninstallservice( "testmachine" )
	kernel.uninstallservice( "memoryusage" )

	-- Fork: the original set Store\ID to "unconfigured" here, for a technician to enter the kiosk number in the
	-- engine's Control Panel. The kiosk profile has set it in step 1.

	if kernel.fileexists( "C:\\REDS\\Kiosk Engine\\bin\\kioskengined.exe" ) then
		kernel.executestring( "C:\\REDS\\Kiosk Engine\\bin\\kioskengined.exe", "--unregister", "C:\\REDS\\Kiosk Engine\\bin", nil )
	end
	if kernel.fileexists( "${REDS}\\Kiosk Engine\\bin\\kioskengined.exe" ) then
		kernel.executestring( "${REDS}\\Kiosk Engine\\bin\\kioskengined.exe", "--unregister", "${REDS}\\Kiosk Engine\\bin", nil )
	end

	kernel.wait( 30000 )

	return true
end


step = create_step( "HAL Maintenance", "Backs up the HAL.xml file and uninstalls any previous version of HAL found." )
step.onrun = function ( self )

    kernel.log( "Export HAL.xml file..." )

	local hal_xml_path = get_hal_path(HalProductID)
	if hal_xml_path ~= nil then
		hal_xml_path = hal_xml_path .. "\\bin\\hal.xml"
		if hal_xml_path ~= nil then
			kernel.filecopy( hal_xml_path, "C:\\hal.xml" )
			kernel.filecopy( hal_xml_path, "c:\\Program Files\\Redbox\\HAL-persistent-configuration.xml")
		end
	end

	return true
end

step = create_step( "Kiosk Engine Configuration", "This kiosk will be set to use the " .. Environment .. " environment."  )
step.onrun = function ( self )

	-- Fork: PROD only, and without BrokerServicesURL and KioskServicesURL (Redbox's blt/bl2.accessredbox.net);
	-- the kiosk profile sets the backend's.
	kernel.log( "Configure for the PROD environment..." )

	kernel.setregistrykey( engine_key .. "\\Remote Services\\Authentication Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Health Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Inventory Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Merchant Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Reservation Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Promotion Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Remote Services\\Store Services", "LiveCall", true )
	kernel.setregistrykey( engine_key .. "\\Store", "Environment", "Production" )
	if not TestMode then
		kernel.setregistrykey("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System","DisableTaskMgr", 1 )
	end

	--After Blink is installed udpate service sometimes times out when the service is starting, so we are extending the timeout from 30 to 60 seconds
	kernel.setdwordregistrykey( "HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control", "ServicesPipeTimeout", 60000 )

	--This registry key makes embedded IE use IE8 rendering rather than the default IE7 rendering for compatability with new routers config pages
	kernel.setdwordregistrykey( "HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Internet Explorer\\Main\\FeatureControl\\FEATURE_BROWSER_EMULATION", "KioskEngine.exe", 8888 )

	kernel.setregistrykey( engine_key .. "\\Store", "RunInitialSync", RunInitialSync )
	kernel.setregistrykey( engine_key .. "\\Store", "InitialSyncType", InitialSyncType )

	kernel.setregistrykey( engine_key .. "\\Remote Services\\Store Services", "HardwareServiceURL", "rcp://127.0.0.1:7001" )

	return true
end

step = create_step( "Activate Image", "The image found in data.zip is activated in this step. Important binary and data files are unpacked into their correct locations." )
step.onrun = function ( self )

	local Repositories = kernel.repositorylist()

	for i,v in pairs( Repositories ) do
		local repo = v

		local pendingchanges = kernel.repositorypendingchanges( repo )
		kernel.log( "Pending Changes for: " .. repo .. " Count: " .. #pendingchanges)

		local pendingactivations = kernel.repositorypendingactivations( repo )
		kernel.log( "Pending Activations for: " .. repo .. " Count: " .. #pendingactivations)

		local beforeactivatescript = nil
		local afteractivatescript = nil

		-- look for activate.lua script
		for i, f in ipairs( pendingactivations ) do
			local myfile = string.lower( f )
			if string.find( myfile, "beforeactivate.lua") ~= nil then
				beforeactivatescript = myfile
			end
			if string.find( myfile, "afteractivate.lua") ~= nil then
				afteractivatescript = myfile
			end
		end


		if #pendingchanges > 0 then
			kernel.log( "kernel.repositoryupdate for: " .. repo .. " Count: " .. #pendingchanges)

			local result = kernel.repositoryupdate( repo )
			if not result.success then
				kernel.log( "kernel.repositoryupdate for repository '" .. tostring( repo ) .. "' failed." )
				kernel.notifyeventerror( self.name, "A999", "kernel.repositoryupdate for repository: " .. repo .. " failed to update." )
				return false
			end

		 end

		 if #pendingactivations > 0 then
			kernel.log( "kernel.repositoryactivate for: " .. repo .. " Count: " .. #pendingactivations)
			if beforeactivatescript ~= nil then
				-- because script will be staged and not activated we must call it from the staged directory
				local scriptlocation = kernel.getdirectoryfrompath(beforeactivatescript)
				kernel.log("kernel.import_script : " .. scriptlocation .. "\\.staged_\\beforeactivate.lua")
				kernel.import_script( scriptlocation .. "\\.staged_\\beforeactivate.lua" )
			end

			local result = kernel.repositoryactivate( repo )
			if not result.success then
				kernel.log( "kernel.repositoryactivate failed, stopping the activation process." )
				kernel.notifyeventerror( self.name, "A999", "kernel.repositoryactivate for repository: " .. repo .. " failed to activate." )
				return false
			end
			if result.success then
				-- only run on success
				if afteractivatescript ~= nil then
					kernel.log("kernel.import_script : " .. afteractivatescript)
					kernel.import_script( afteractivatescript )
				end
			end
		end

	end

	return true
end

-- Fork: new. Activating the image installed the packages' own configuration files (appsettings*.json, the
-- update manager's .config), so the profile is applied again to point them at the backend.
step = create_step( "Point Kiosk at Backend", "The services installed from the image are set up for the backend in the kiosk profile." )
step.onrun = function( self )
	kernel.log( "Apply the kiosk profile to the activated image..." )
	return apply_kiosk_profile( self )
end

step = create_step( "Install HAL", "The Hardware Abstraction Layer is install in this step. Binaries are unpacked and a windows service is installed." )
step.onrun = function ( self )
	-- Fork: HAL failed at start-up on a fresh PC: HAL.PackageInstaller installs HAL.xml.default (0 decks), and halsvc
	-- then stops in DecksManager and cannot be stopped normally. The kiosk's hal.xml (next to setup.exe, copied to
	-- Installers) goes in first; otherwise the one this PC had before (HAL Maintenance's C:\hal.xml).
	kernel.stopservice( 'HalSvc$Default' )
	kernel.killprocessbyname( "halsvc-win32" )
	if kernel.fileexists( installers .. "\\hal.xml" ) then
		kernel.log( "Installing the kiosk's hal.xml from " .. installers )
		kernel.filecopy( installers .. "\\hal.xml", hal_bin .. "\\hal.xml" )
	elseif kernel.fileexists( "C:\\hal.xml" ) and not kernel.fileexists( hal_bin .. "\\hal.xml" ) then
		kernel.log( "Installing the hal.xml saved by HAL Maintenance" )
		kernel.filecopy( "C:\\hal.xml", hal_bin .. "\\hal.xml" )
	elseif not kernel.fileexists( hal_bin .. "\\hal.xml" ) then
		kernel.log( "WARNING: no hal.xml for this kiosk; HAL gets HAL.xml.default (0 decks) and will not start until the kiosk's hal.xml is installed" )
	end

	-- Fork: HAL.PackageInstaller and halsvc are .NET 2.0 programs; the original needed .NET 3.5 for them (and they
	-- did not run without it, unnoticed). They, and any other .NET 2.0 exe of the image, now run on .NET 4.8.
	local retcode = run_powershell( "retarget-netfx2.ps1", "-Root \"${RedboxProgramFiles}\"", 600000 )
	if retcode ~= 0 then
		return fail_step( self, "H001", "The .NET 2.0 programs could not be set up to run on .NET 4.8.", "See Update Manager\\logs\\retarget-netfx2.log." )
	end

	kernel.log("Executing HAL.PackageInstaller.exe.")
	retcode = run_logged( hal_bin .. "\\HAL.PackageInstaller.exe", "", hal_bin, 300000, "hal-package-installer.log" )
	kernel.log("Finsihed executing HAL.PackageInstaller.exe. Return code:" .. retcode)

	kernel.wait( 5000 )
	kernel.stopservice( 'HalSvc$Default' )
	-- Fork: the original did not look at the result.
	if not kernel.serviceexists( 'halsvc$default' ) then
		return fail_step( self, "H002", "HAL.PackageInstaller did not install the HAL service.", "Exit code " .. tostring( retcode ) .. "; see Update Manager\\logs\\hal-package-installer.log." )
	end
	return true
end

-- add
step = create_step( "Setup Card Reader", "Values for card reader verification are inserted into the registry here." )
step.onrun = function( self )
	kernel.log( "Executing card reader registry key entries..." )
	kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Desktop\\WindowsMetrics","ScrollWidth", -375 )
	kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Desktop\\WindowsMetrics","ScrollHeight", -375 )
	kernel.setregistrykey("HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\CrashControl", "AutoReboot",  true )
	-- Fork: Redbox's card reader list (HKLM\SOFTWARE\Redbox\CRList) and AuditPol.exe are left out.

	-- Fork: without a card reader (a VM, Windows Sandbox) the rental application goes to maintenance mode
	-- ("Credit Card Reader Communications Error"); use the engine's Device Service simulator instead, as
	-- Install-Kiosk.ps1 does (Hardware.SimulateCardReader).
	if SimulateCardReader then
		kernel.log( "Simulated card reader (test mode)" )
		local sim = engine_key .. "\\Remote Services\\Device Service Simulator"
		kernel.setregistrykey( sim, "UseSimulatedCardReader", "True" )
		kernel.setregistrykey( sim, "IsCardReaderTampered", "False" )
		kernel.setregistrykey( sim, "IsConnectedToDeviceService", "True" )
		kernel.setregistrykey( sim, "IsCardReaderConnected", "True" )
		-- Its keyboard test cards; without the file a key press on the start view ends the engine.
		local cards = "${REDS}\\Kiosk Engine\\data\\KioskEngineSimulatedCardReaderData.json"
		if not kernel.fileexists( cards ) then
			kernel.filewritealltext( cards, "{\"CurrentCard\":null,\"StoredCardData\":{}}" )
		end
	end

	return true
end

-- add
step = create_step( "Install lock screen wallpaper", "The wallpaper used on the Windows lock screen is inserted here." )
step.onrun = function( self )
	kernel.log( "Installing lock screen wallpaper...." )
	local wallpaper = "${REDS}\\Installers\\backgrounddefault.jpg"
	if kernel.fileexists( wallpaper ) then
		kernel.setregistrykey(
			"HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\LogonUI\\Background", "OEMBackground", "1" )
		kernel.log( "Copying wallpaper to C:\\Windows\\system32\\oobe\\info\\backgrounds\\backgrounddefault.jpg" )
		if kernel.directoryexists( "C:\\Windows\\system32\\oobe\\info\\backgrounds" ) ~= true then
			kernel.directorycreate( "C:\\Windows\\system32\\oobe\\info\\backgrounds", true )
		end
		kernel.filecopy( wallpaper, "C:\\Windows\\system32\\oobe\\info\\backgrounds\\backgrounddefault.jpg" )
	else
		-- Fork: the wallpapers are optional in this installer; the original failed here without them.
		kernel.log( "Wallpaper file " .. wallpaper .. " not in this installer; lock screen left as it is" )
	end

	local shellwallpaper = "${REDS}\\Installers\\background.bmp"
	if kernel.fileexists( shellwallpaper ) then
		if kernel.directoryexists( "C:\\redbox" ) ~= true then
		    kernel.directorycreate( "C:\\redbox", true )
		end
		kernel.log( "Copying shellwallpaper to C:\\redbox\\background.bmp" )
		kernel.filecopy( shellwallpaper, "C:\\redbox\\background.bmp" )
	end

	return true
end

-- add
step = create_step( "Install Redbox Shell", "The Redbox Replacement for Windows 10 shell package." )
step.onrun = function ( self )
    kernel.log( "Remove Bootstrap Windows Scheduled Task..." )
	kernel.win32removescheduledtask( "Bootstrap Store" )
    kernel.log( "Configure Kiosk Shell..." )
    kernel.directorycreate( "${RedboxProgramFiles}\\KioskShell" )

	local zipFile = "${REDS}\\Installers\\KioskShell.zip"
	if kernel.fileexists( zipFile ) then
		kernel.log( "Unzipping " .. zipFile .. " to " .."${RedboxProgramFiles}\\KioskShell" )
		kernel.unzip( zipFile, "${RedboxProgramFiles}\\KioskShell" );
	else
		kernel.log( "Kiosk shell installation: " .. zipFile .. " file not found" )
		return false
	end

	-- Fork: kernel.replaceshellwith ran wmic.exe, which Windows 11 does not have, and the step ignored that it
	-- failed. set-kiosk-shell.ps1 makes the same Shell Launcher calls through CIM, or sets the Winlogon shell
	-- where the edition has no Shell Launcher (Pro), and the step now fails when the shell is not set.
	local retcode = run_powershell( "set-kiosk-shell.ps1", "-Shell \"${RedboxProgramFiles}\\KioskShell\\KioskShell.exe\" -Mode " .. ShellMode, 300000 )
	if retcode ~= 0 then
		return fail_step( self, "S001", "KioskShell could not be made the shell.", "See Update Manager\\logs\\set-kiosk-shell.log." )
	end

    kernel.log( "Disable DrWatson..." )
    kernel.setregistrykey(
        "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug",
        "Auto",
        0 )
	kernel.setregistrykey(
		"HKEY_LOCAL_MACHINE\\SOFTWARE\\Redbox\\REDS\\Kiosk Engine\\Store",
		"WatchDogEnabled",
		"true" )
	return true
end

-- add
step = create_step( "Mute PC sound", "Muting PC sound." )
step.onrun = function( self )
   kernel.log( "Muting sound..." )
   kernel.mutesound()
   return true
end

step = create_step( "Register Update Manager", "Install and register update manager service." )
step.onrun = function ( self )
    kernel.log( "Register Update Manager Service..." )
    kernel.executestring( "${REDS}\\Update Manager\\bin\\updatemgrd.exe", "--register", "${REDS}\\Update Manager\\bin", nil )
	-- Fork: check the result.
	if not kernel.serviceexists( "updatemgrd$default" ) then
		return fail_step( self, "U001", "updatemgrd --register did not create the updatemgrd$default service." )
	end
    return true
end

step = create_step( "Device Service Setup", "Update Reader To Latest, Install and Register Device Service" )
step.onrun = function ( self )
	local rbaInstallerFolderPath = "${REDS}\\DeviceService\\bin"
	local rbaInstallerFileName = "RBAInstaller.exe"
	local rbaInstallerFullPath = rbaInstallerFolderPath .. "\\" .. rbaInstallerFileName
	local installerExists = kernel.fileexists(rbaInstallerFullPath);
	kernel.log("Device Service RBAInstaller exists: " .. tostring(installerExists))
	-- Fork: the firmware update (6 minutes without a reader) can be switched off; it is in test mode.
	if installerExists and UpdateCardReaderFirmware then
		local service_exists = kernel.serviceexists("device$service");
		if service_exists then
			kernel.log("Stopping device service")
			kernel.stopservice( "device$service" )
			kernel.wait( 20000 )
		end

		retcode = kernel.executestring(rbaInstallerFullPath,"",rbaInstallerFolderPath,900000)
		kernel.log("Finished executing RBAInstaller.exe. Return code:" .. retcode)

		if service_exists then
			kernel.log("Starting device service")
			kernel.startservice( "device$service" )
		end
	else
		kernel.log( "RBAInstaller.exe does not exist or UpdateCardReaderFirmware is off, Skipping." )
	end

	kernel.log( "Register Device Service..." )

	if kernel.fileexists( rbaInstallerFolderPath .."\\DeviceService.WebApi.exe" ) then
		kernel.executestring( "c:\\windows\\system32\\sc.exe", "create device$service displayname= \"Redbox Device Service\" start= delayed-auto binpath= \"" .. rbaInstallerFolderPath .. "\\DeviceService.WebApi.exe\"", rbaInstallerFolderPath, nil )
		if not kernel.serviceexists( "device$service" ) then
			return fail_step( self, "D001", "sc create did not create the device$service service." )
		end
	else
		kernel.log( "DeviceService.WebAPI.exe does not exist, Skipping." )
	end

    return true
end

step = create_step( "Register Kiosk Client Service", "Install and register Kiosk Client service." )
step.onrun = function ( self )
	kernel.log( "Register Kiosk Client Service..." )

	local path = "C:\\Program Files\\Redbox\\REDS\\Kiosk Client\\bin\\KioskClientService.Api.exe"
	local service_exists = kernel.fileexists(path);

	if (kernel.fileexists(path)) then
		kernel.log("Installing kiosk-client as a windows service since it did not exist in beforeactivate")
		local scPath = "c:\\windows\\system32\\sc.exe"
		local options = 'create kioskclient$service displayname= "Redbox Kiosk Client Service" start= delayed-auto binpath= "C:\\Program Files\\Redbox\\REDS\\Kiosk Client\\bin\\KioskClientService.Api.exe"'
		local installPath = "c:\\Program Files\\Redbox\\Reds\\Kiosk Client\\bin"
		retcode = kernel.executestring(scPath,options,installPath,300000)
		kernel.log("Finished executing service install. Return code:" .. retcode)
		if not kernel.serviceexists( "kioskclient$service" ) then
			return fail_step( self, "K001", "sc create did not create the kioskclient$service service." )
		end

		kernel.log("running afteractivate.lua for kioskclient")

		local dllPath = "c:\\Program Files\\Redbox\\Reds\\Kiosk Client\\bin\\"
		local dllSource = dllPath .. "m.VisualStudio.Web.CodeGeneration.EntityFrameworkCore.dll";
		local dllDest = dllPath .. "Microsoft.VisualStudio.Web.CodeGeneration.EntityFrameworkCore.dll";

		if (kernel.fileexists(dllSource)) then
			kernel.filecopy( dllSource, dllDest )
			kernel.log("kiosk client - repo.afteractive: finished copying file that was too long for update service 64 char limit")
		end

	else
		kernel.log( "KioskClientService.WebAPI.exe does not exist, Skipping." )
	end

    return true
end

step = create_step( "Register Update Client Service", "Install and register Update Client service." )
step.onrun = function ( self )
	kernel.log( "Register Update Client Service..." )

	local path = "C:\\Program Files\\Redbox\\REDS\\Update Client\\bin\\UpdateClientService.API.exe"

	if (kernel.fileexists(path)) then
		local scPath = "c:\\windows\\system32\\sc.exe"
		local options = 'create updateclient$service displayname= "Redbox Update Client Service" start= delayed-auto binpath= "C:\\Program Files\\Redbox\\REDS\\Update Client\\bin\\UpdateClientService.API.exe"'
		local installPath = "c:\\Program Files\\Redbox\\Reds\\Update Client\\bin"
		retcode = kernel.executestring(scPath,options,installPath,300000)
		kernel.log("Finished executing service install. Return code:" .. retcode)
		if not kernel.serviceexists( "updateclient$service" ) then
			return fail_step( self, "C001", "sc create did not create the updateclient$service service." )
		end

		kernel.log("running afteractivate.lua for UpdateClientService")
	else
		kernel.log( "UpdateClientService.API.exe does not exist, Skipping." )
	end

    return true
end

-- add
step = create_step( "Turn off Autorun", "Turning off Autorun." )
step.onrun = function( self )
	kernel.log("Setting NoDriveTypeAutoRun registry key to turn off auto run")
	kernel.setdwordregistrykey( "HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\policies\\Explorer", "NoDriveTypeAutoRun", 255 )
	return true
end

--add
step = create_step("Windows 7 migration - update manager scheduling.", "This will restore the original update manager job scheduling preserved prior to a migration from Windows XP to Windows 7.")
step.onrun = function(self)
	local reg_key = "HKEY_LOCAL_MACHINE\\Software\\Redbox\\REDS\\Kiosk Engine\\Core"
	local reg_name = "SchedulerEntries"
	local schedule = "[{\"JobName\":\"StatsMediumTimer\",\"Label\":null,\"StartTime\":{\"theDate\":634420306327357576,\"dateTimeKind\":\"Utc\",\"dateString\":\"2011-05-26T18:17:12.7357576Z\"},\"EndTime\":null,\"CronExpression\":\"19 28 1-23/2 * * ? \",\"FunctionName\":\"stats_run_medium_timer\",\"Program\":null,\"MisfireInstruction\":\"FireOnceNow\",\"Parameters\":[]},{\"JobName\":\"StatsDailyTimer\",\"Label\":null,\"StartTime\":{\"theDate\":634420306328358576,\"dateTimeKind\":\"Utc\",\"dateString\":\"2011-05-26T18:17:12.8358576Z\"},\"EndTime\":null,\"CronExpression\":\"19 28 3 * * ? \",\"FunctionName\":\"stats_run_daily_timer\",\"Program\":null,\"MisfireInstruction\":\"FireOnceNow\",\"Parameters\":[]}]"
	kernel.setregistrykey(reg_key, reg_name, schedule)
	kernel.log("Created registry key: " .. reg_key .. "\\" .. reg_name .. " = " .. schedule .. ".")
	return true
end

-- add
step = create_step( "Quoted Strings", "All service paths in the registry must have quoted strings for PCI." )
step.onrun = function ( self )
	kernel.log("Running PCI-QuotedStrings.lua")

	local path = "HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Services"
	local keyName = "ImagePath"

	local twRegSvc = "TwRegSvc"
	local kaseyaAgent = "KaseyaAgent"
	local hal = "halsvc$default"

	local twRegSvcValue = "C:\\PROGRAM FILES\\MICROTOUCH\\MT 7\\TwRegSvc.exe /Service"
	local kaseyaAgentValue = "c:\\Program Files\\Kaseya\\Agent\\AgentMon.exe"
	local halValue = "C:\\Program Files\\Redbox\\HALService\\bin\\halsvc-win32.exe"

	local twRegSvcNewValue = "\"C:\\PROGRAM FILES\\MICROTOUCH\\MT 7\\TwRegSvc.exe\" /Service"
	local kaseyaAgentNewValue = "\"c:\\Program Files\\Kaseya\\Agent\\AgentMon.exe\""
	local halNewValue = "\"C:\\Program Files\\Redbox\\HALService\\bin\\halsvc-win32.exe\""


	FixService(path, twRegSvc, keyName, twRegSvcValue, twRegSvcNewValue)
	FixService(path, kaseyaAgent, keyName, kaseyaAgentValue, kaseyaAgentNewValue)
	FixService(path, hal, keyName, halValue, halNewValue)

	return true
end

-- add
step = create_step( "Disable Sticky Keys", "Disable Sticky Keys for PCI." )
step.onrun = function ( self )
	kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "AutoRepeatDelay", 0)
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "AutoRepeatRate", 0)
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "BounceTime", 0)
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "DelayBeforeAcceptance", 1000)
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "Flags", 122)
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "Last BounceKey Setting", "00000000" )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "Last Valid Delay", "00000000" )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "Last Valid Repeat", "00000000" )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\Keyboard Response", "Last Valid Wait", "000003e8" )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\HighContrast", "Flags", 4218 )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\MouseKeys", "Flags", 58 )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\MouseKeys", "MaximumSpeed", 68 )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\MouseKeys", "TimeToMaximumSpeed", 3000 )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\StickyKeys", "Flags", 506 )
    kernel.setregistrykey("HKEY_CURRENT_USER\\Control Panel\\Accessibility\\ToggleKeys", "Flags", 58 )

	return true
end

-- add
step = create_step( "Final Image Maintenance", "Once this step completes you may review your results or exit this window by clicking the X in the top right." )
step.onrun = function ( self )
	if kernel.fileexists( "C:\\images.bat" ) then
	   kernel.filemove( "C:\\images.bat", "C:\\images.bat.from.image" )
	end

	kernel.markscriptcomplete()
	-- Fork: in test mode (a VM, Windows Sandbox) start everything now instead of rebooting, as Install-Kiosk.ps1's
	-- StartAfterInstall does: the services, then the engine in this session.
	if TestMode then
		kernel.log( "Installation complete; test mode: starting the services and the engine..." )
		for i, name in ipairs( { "halsvc$default", "updatemgrd$default", "device$service", "kioskclient$service", "updateclient$service" } ) do
			if kernel.serviceexists( name ) then
				kernel.startservice( name )
				kernel.log( "..." .. name .. ": " .. tostring( kernel.getservicestatus( name ) ) )
			end
		end
		local engine = "${REDS}\\Kiosk Engine\\bin\\kioskengine.exe"
		if kernel.fileexists( engine ) then
			kernel.killprocessbyname( "kioskengine" )
			kernel.executestring( engine, "", "${REDS}\\Kiosk Engine\\bin", 0 )
			kernel.log( "...kioskengine.exe started" )
		end
	else
	    kernel.log( "Installation complete; rebooting..." )
		if silent == false then
			kernel.reboot()
		end
	end
	Results.success = true
	Results.message = "post-install.lua complete."
	return true
end

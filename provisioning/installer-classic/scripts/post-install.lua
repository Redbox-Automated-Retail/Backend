--
-- post-install.lua
-- Copyright (C) 2009 Redbox Automated Retail, LLC.
-- All Rights Reserved
--
-- installer-classic fork: unchanged except for this comment. installer-heading.lua (written by
-- Build-ClassicInstaller.ps1) sets Environment and the fork's settings; installer-steps.lua holds the steps.
--
Results = {}

steps = {}

Environment = "QA"
Action = "Install"
RunInitialSync = false
InitialSyncType = "background"

HalProductID = "{92E5D7C8-C00B-4416-9120-FE11C59C984C}"
HalVersion = kernel.getregistrykey( "HKEY_LOCAL_MACHINE\\SOFTWARE\\Redbox\\HAL", "Product Version" )

function create_step( name, description )
    local t = {}
	table.insert(steps, t)
    t.name = name
    t.description = description
	t.onrun = function( self ) end
	kernel.log( "registering " .. name )
    return t
end

kernel.log("Importing installer-utils.lua script.")
kernel.import_script( "${Scripts}\\installer-utils.lua" )

kernel.log("Importing installer-header.lua script.")
kernel.import_script( "${Scripts}\\installer-heading.lua" )

kernel.log("Importing installer-steps.lua script.")
kernel.import_script( "${Scripts}\\installer-steps.lua" )

local step_table = {}

step_table.resume = function( self )

	local last_executed_step = 0
	if kernel.fileexists( "C:\\.post-install" ) then
		last_executed_step = tonumber( kernel.filereadalltext( "C:\\.post-install" ) )
        if last_executed_step == #steps then
			kernel.notifyaddevent( "Installer Complete", "The installer has already beeen run successfully. To run again delete the .post-install file in the root of the C drive." )
            kernel.notifyeventcomplete( "Installer Complete" )

            kernel.log( "post-install is complete; removing script from queue." )
            kernel.markscriptcomplete()
            Results.success = true
            Results.message = "post-install.lua complete."
            return
        end
		kernel.log( ".post-install file found, resuming at step: " .. last_executed_step + 1 )
	else
		kernel.log( ".post-install file not found, resuming from first step." )
	end

	for i = last_executed_step + 1, #steps do
		kernel.log("Add Event: " .. steps[i].name)
		kernel.notifyaddevent( steps[i].name, steps[i].description )
	end

	kernel.log("last executed step " .. last_executed_step)
	kernel.log("step count " .. #steps)


	for i = last_executed_step + 1, #steps do
		local step = steps[i]
		if step ~= nil then

			kernel.log( "Executing Step " .. tostring( i ) .. " " ..step.description )
			kernel.notifyeventstart( step.name )
			local success = step:onrun()

			if success then
				kernel.notifyeventcomplete( step.name )
			else
				kernel.log( "...Step returned false for success, stopping." )
				return
			end
			kernel.filewritealltext( "C:\\.post-install", tostring( i ) )
		end
	end

end



kernel.log("Environment: " .. Environment)
if (HalProductID ~= nil) then
	kernel.log("Hal ProductID: " .. HalProductID)
end
if (HalVersion ~= nil) then
	kernel.log("Hal Version: " .. HalVersion)
end


step_table:resume()
kernel.notifyexit()

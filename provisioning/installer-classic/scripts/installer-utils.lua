--
-- installer-utils.lua
-- Copyright (C) 2009 Redbox Automated Retail, LLC.
-- All Rights Reserved
--
-- installer-classic fork: the original helpers, plus run_logged, run_powershell and fail_step at the end.
--

function delete_files( path, pattern, invalid_paths )
	local candidate_files = kernel.directorygetfiles( path, pattern, true )
	local directories_to_clean = {}
	for k, v in ipairs( candidate_files ) do
		kernel.log( v )
		local allow_delete = true
		for i, j in ipairs( invalid_paths ) do
			local _start, _end = string.find( string.lower( v ), string.lower( j ) )
			if _start ~= nil then
				allow_delete = false
				break
			end
		end
		if allow_delete then
			kernel.filedelete( v )
			table.insert( directories_to_clean, kernel.getdirectoryname( v ) )
		end
	end
	for k, v in ipairs( directories_to_clean ) do
		if kernel.directoryexists( v ) then
			local remaining_files = kernel.directorygetfiles( v, "*.*", true )
			if #remaining_files == 0 then
				kernel.directoryremove( v, true )
			end
		end
	end
end

function delete_folder( path, pattern, invalid_paths )
	local directories_to_remove = kernel.directorygetdirectories( path, pattern, true )
	for k, v in ipairs( directories_to_remove ) do
		kernel.log( "Directory: " .. v )
		local allow_delete = true
		for i, j in ipairs( invalid_paths ) do
			local _start, _end = string.find( string.lower( v ), string.lower( j ) )
			if _start ~= nil then
				allow_delete = false
				break
			end
		end
		if allow_delete then
			kernel.directoryremove( v, true )
			kernel.log( "...Removed" )
		else
			kernel.log( "...Skipped because it's in the invalid_paths table." )
		end
	end
end


function execute_file_if_exists( path, args, working, timeout )
    kernel.log( "Executing installer: " .. path .. " with arguments: " .. args )
    if kernel.fileexists( path ) then
        kernel.executestring(
            path,
            args,
            working,
            timeout )
        return true
    end
    return false
end

function wait_for_service_status( name, status )
    local attempts = 0
    while kernel.getservicestatus( name ) ~= status do
        kernel.wait( 2000 )
        attempts = attempts + 1
        if attempts > 150 then
            kernel.log( "Service " .. name .. " did not transition to status " .. status )
            return false
        end
    end
    return true
end

function get_hal_path( HalProductID )
    local silent_uninstall_path = kernel.getregistrykey(
        "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" .. HalProductID,
        "SilentUninstallString" )
    if silent_uninstall_path ~= nil then
        kernel.log( "hal_path = " .. silent_uninstall_path )
        local _start, _end = string.find( silent_uninstall_path, "-" )
        local hal_path = kernel.getdirectoryfrompath( string.sub( silent_uninstall_path, 1, _start - 2 ) )
        if hal_path ~= nil then
            local config_file_path = hal_path
            kernel.log( "get_hal_path = " .. config_file_path )
            return config_file_path
        end
    end
    return nil
end

function FixService( regPath, regPath2, name, value, newValue )
    local fullPath = regPath .. "\\" .. regPath2
    local key = kernel.getregistrykey( fullPath, name)
    if key == nil then
    	kernel.log("Quoted service registry path not found - skipping." .. fullPath)
    	return 
    end
    if (string.lower(value) == string.lower(key)) then
		kernel.setregistrykey(fullPath, name, newValue )   
  	 	kernel.log("Quoted service registry path found and updated with quotes." .. fullPath .. " Key: " .. key)
    else
  	 	kernel.log("Quoted service registry path found but value is already quoted." .. fullPath .. " Key: " .. key)
    end
    return 
end

-- Fork helpers -------------------------------------------------------------------------------------------------

-- The front end is an x86 process: System32 would be redirected to SysWOW64, and the 32-bit PowerShell sees the
-- 32-bit registry view and no Shell Launcher WMI provider.
function system32_path()
    if kernel.directoryexists( "C:\\Windows\\Sysnative" ) then
        return "C:\\Windows\\Sysnative"
    end
    return "C:\\Windows\\System32"
end

-- Run a program with its output appended to Update Manager\logs\<logname>, and return its exit code
-- (-1 when it did not finish within timeout milliseconds).
function run_logged( path, args, working, timeout, logname )
    local log = "${REDS}\\Update Manager\\logs\\" .. logname
    kernel.log( "Executing: \"" .. path .. "\" " .. args .. " (output in " .. log .. ")" )
    local retcode = kernel.executestring(
        system32_path() .. "\\cmd.exe",
        "/c \"\"" .. path .. "\" " .. args .. " >> \"" .. log .. "\" 2>&1\"",
        working,
        timeout )
    kernel.log( "...exit code " .. tostring( retcode ) )
    return retcode
end

-- Run one of the fork's PowerShell helpers (Update Manager\scripts\<script>) with the 64-bit PowerShell. run_logged
-- starts it from the 64-bit cmd.exe, which sees System32 as it is (and no Sysnative).
function run_powershell( script, args, timeout )
    return run_logged(
        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "-NoProfile -ExecutionPolicy Bypass -File \"${Scripts}\\" .. script .. "\" " .. args,
        "${Scripts}",
        timeout,
        ( string.gsub( script, "%.ps1$", ".log" ) ) )
end

-- Report a failed step in the front end's event list (and in a message box unless silent) and return false,
-- which stops the installation; the next run resumes at this step (C:\.post-install).
function fail_step( step, code, message, details )
    kernel.log( "FAILED: " .. message .. " " .. ( details or "" ) )
    kernel.notifyeventerror( step.name, code, message, details or "" )
    if not kernel.fileexists( "c:\\Program Files\\Redbox\\XPTo7\\silent.txt" ) then
        kernel.notifyinfo( "Error", message )
    end
    return false
end

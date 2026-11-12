import json
import os
import sys
import time
import re
import traceback

SUCCESS_EXIT_CODE = 0
FAIL_EXIT_CODE = 1
FSP_MINOR_MISMATCH_EXIT_CODE = 2
FSP_MAJOR_MISMATCH_EXIT_CODE = 3

start_time = time.time()

# load necessary modules
loadModule('/System/Resources')
loadModule('/FSP/ProjectGen')
loadModule('/FSP/Configuration')

if len(sys.argv) < 3:
    print('[ERROR] Arguments missing. Usage: <projectName> <configFile>')
    exit(1)


# ----------------------------
# Handle command line arguments
# ----------------------------
def get_arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


projectName = get_arg('--project_name')
configFile = get_arg('--config_file')
selectedSensorType = get_arg('--selected_sensor_type')
selectedShuntType = get_arg('--selected_shunt_type')
boardName = get_arg('--board_name')
familyName = get_arg('--family_name')
requiredFspVersion = get_arg('--required_fsp_version')

# determine the root folder where project folders will be created
workspaceRoot = getWorkspace().getLocation().toOSString()
print('working with projects at ' + workspaceRoot)

# get config file data
with open(configFile) as configMap:
    configData = json.load(configMap)


def getModuleFactoryId(module):
    return re.sub(r"\.\d+$", "", module.getId())


def isExactModuleFactory(module, expectedModuleId):
    if module is None or not expectedModuleId:
        return False
    return getModuleFactoryId(module) == expectedModuleId


def findModule(moduleId, projectConfig, stackModule):
    for thread in projectConfig.getThreads():
        # print("-" * 50)
        # print('Searching for: ' + moduleId)
        # print('Searching in thread ' + thread.getDisplay())
        for stack in thread.getStacks():
            if isExactModuleFactory(stack, stackModule):
                # print("")
                # print("\t" + 'Searching in stack ' + stack.getId())
                module = findModuleInStack(moduleId, stack)
                if module is not None:
                    # print("")
                    # print("\t" + 'Found module ' + module.getDisplay())
                    return module
            # else:
            #     print("")
            #     print("\t" + 'Skip stack ' + stack.getId())


def findModuleInStack(moduleId, stack, depth=0):
    indent1 = "\t" * (depth + 1)
    indent2 = "\t" * (depth + 2)
    indent3 = "\t" * (depth + 3)
    if getModuleFactoryId(stack) == moduleId:
        # print(f"{indent2} Found module: {stack.getDisplay()}")
        return stack

    # interface_list = []
    # interfaceList = stack.getInterfaces()
    # for interface in interfaceList:
    #     interface_list.append(interface.getDisplay())
    # print(f"{indent1}Stack interface: {interface_list}")

    for iface in stack.getInterfaces():
        if iface.hasModule():
            # print("")
            # print(indent2 + "-" * 50)
            # print(indent2 + f"Searching in stack interface: {iface.getDisplay()}")
            # print(indent2 + f"Searching in stack interface module: {iface.getModule().getDisplay()}")
            child = findModuleInStack(moduleId, iface.getModule(), depth + 1)
            if child is not None:
                return child
        #     else:
        #         print(indent2 + f"Not found module {moduleId}")
        # else:
        #     print("")
        #     print(indent2 + "-" * 50)
        #     print(indent2 + f"Searching in stack interface: {iface.getDisplay()}")
        #     print(
        #         indent3 + f"Not found module '{moduleId}' in Interface '{iface.getDisplay()}' cause this interface does not have module")


def getApplicableSensorTypeId(moduleConfig):
    value = moduleConfig.get("applicableSensorTypeId")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid applicableSensorTypeId: " + repr(value))
    return value.strip()


def isSensorSpecificModuleApplicable(moduleConfig):
    applicableSensorTypeId = getApplicableSensorTypeId(moduleConfig)
    if applicableSensorTypeId is None:
        return True
    if not selectedSensorType:
        return False
    return applicableSensorTypeId == selectedSensorType


def findSelectedDefinition(items, idField, selectedId, label):
    if not selectedId:
        return None
    matches = [item for item in items if item.get(idField) == selectedId]
    if len(matches) != 1:
        raise ValueError("Expected exactly one " + label + " definition for " + repr(selectedId) + "; found " + str(len(matches)))
    return matches[0]


def validateIncompatibleSelection(boardData):
    if not selectedSensorType or not selectedShuntType:
        return
    for index, item in enumerate(boardData.get("incompatibleSelections") or []):
        if not isinstance(item, dict):
            raise ValueError("Invalid incompatibleSelections entry at index " + str(index))
        sensorTypeId = item.get("sensorTypeId")
        shuntTypeId = item.get("shuntTypeId")
        if not sensorTypeId or not shuntTypeId:
            raise ValueError("Incomplete incompatibleSelections entry at index " + str(index))
        if sensorTypeId == selectedSensorType and shuntTypeId == selectedShuntType:
            raise ValueError("Incompatible sensor/shunt selection: " + selectedSensorType + " / " + selectedShuntType)


def conditionMatches(when):
    if not isinstance(when, dict) or not when:
        return False
    unknownKeys = set(when) - {"sensorTypeId", "shuntTypeId"}
    if unknownKeys:
        raise ValueError("Unsupported warning condition keys: " + ", ".join(sorted(unknownKeys)))
    if "sensorTypeId" in when and when["sensorTypeId"] != selectedSensorType:
        return False
    if "shuntTypeId" in when and when["shuntTypeId"] != selectedShuntType:
        return False
    return True


def reportMatchingWarnings(boardData):
    for index, warning in enumerate(boardData.get("warnings") or []):
        if not isinstance(warning, dict):
            raise ValueError("Invalid warnings entry at index " + str(index))
        messageId = warning.get("messageId")
        if not messageId or not isinstance(messageId, str):
            raise ValueError("Invalid warning messageId at index " + str(index))
        if conditionMatches(warning.get("when")):
            print("[WARN] Applicable warning messageId: " + messageId)


def validateSensorMarkers(boardData):
    known = {item.get("sensorTypeId") for item in boardData.get("deviceSensorTypes") or []}
    legacy = {"isSensorLess", "isHall", "isAFO", "isHFI"}
    for shunt in boardData.get("devicesShuntTypes") or []:
        for cfg in shunt.get("moduleConfigs") or []:
            found = legacy.intersection(cfg)
            if found:
                raise ValueError("Legacy sensor marker is not supported: " + ", ".join(sorted(found)))
            sid = getApplicableSensorTypeId(cfg)
            if sid is not None and sid not in known:
                raise ValueError("Unknown applicableSensorTypeId: " + sid)

def createStack(projectConfig, moduleId, stackModule):
    if moduleId:
        module = findModule(moduleId, projectConfig, stackModule)
        if module is None:
            halThread = projectConfig.getThreads()[0]
            module = halThread.createStack(moduleId)
            print("\t" + "-" * 50)
            print("\t" + 'Creating ' + module.getId())
        else:
            print("\t" + "-" * 50)
            print("\t" + 'using ' + module.getId())
        return module
    return None


def createStackWithParent(projectConfig, moduleId, stackModule, parentModuleId, interfaceId):
    parentModule = findModule(parentModuleId, projectConfig, stackModule)
    if parentModule is None:
        raise ValueError("Parent module not found: " + str(parentModuleId))
    print("\t" + "-" * 50)
    print("\t" + "Start searching for child module - " + moduleId + " in parent module - " + parentModule.getId())
    for iface in parentModule.getInterfaces():
        if iface.getId() != interfaceId:
            continue
        if iface.hasModule():
            connectedModule = iface.getModule()
            if isExactModuleFactory(connectedModule, moduleId):
                print("\t" * 3 + "using " + connectedModule.getId())
                return connectedModule
            print("\t" * 2 + "Deleting Iface module: " + connectedModule.getId())
            iface.deleteModule()
        module = iface.createModule(moduleId)
        print("\t" * 3 + "Creating " + module.getId())
        return module
    raise ValueError("Interface not found: " + str(interfaceId) + " in parent module " + str(parentModuleId))

def deleteStackWithParent(projectConfig, stackModule, parentModuleId, interfaceId, expectedModuleId=None):
    parentModule = findModule(parentModuleId, projectConfig, stackModule)
    if parentModule is None:
        print("[WARN] Parent module not found: " + str(parentModuleId))
        return False

    for iface in parentModule.getInterfaces():
        if iface.getId() == interfaceId:
            if not iface.hasModule():
                print(
                    "No module is assigned to interface: "
                    + iface.getId()
                )
                return True
            connectedModule = iface.getModule()

            if (expectedModuleId is not None and not isExactModuleFactory(connectedModule, expectedModuleId)):
                print("[ERROR] Unexpected module on interface: " + connectedModule.getId())
                return False

            print("Deleting interface module: " + connectedModule.getId())
            iface.deleteModule()
            return True

    print("[WARN] Interface not found: " + str(interfaceId))
    return False

def isLockedPropertyException(exception):
    return "Property is locked:" in str(exception)


def setModuleProperty(module, propId, propValue, propIncludeValue, propExcludeValue):
    if module is None:
        raise ValueError("Property target module is None: " + str(propId))

    for prop in module.getProperties():
        if prop.getId() != propId:
            continue

        print("\t" * 2 + 'Setting property: ' + propId)

        isLocked = prop.isLocked()
        print("\t" * 3 + 'Property isLocked(): ' + str(isLocked))
        if isLocked:
            print("\t" * 3 + 'Skipping locked property: ' + propId)
            return

        hasIncludeKey = propIncludeValue is not None
        hasExcludeKey = propExcludeValue is not None
        includeValues = propIncludeValue if propIncludeValue is not None else []
        excludeValues = propExcludeValue if propExcludeValue is not None else []
        isBitmapEntry = hasIncludeKey or hasExcludeKey
        hasBitmapChanges = len(includeValues) > 0 or len(excludeValues) > 0

        if propValue is not None and isBitmapEntry:
            raise ValueError(
                "JSON property contains both value and includeValue/excludeValue: "
                + propId
            )

        if isBitmapEntry:
            if not prop.isBitmap():
                raise ValueError(
                    "JSON uses includeValue/excludeValue for non-bitmap property: "
                    + propId
                )

            if not hasBitmapChanges:
                print("\t" * 3 + 'No bitmap changes; skipping property: ' + propId)
                return

            for includeValue in includeValues:
                if not prop.isIncludedOption(includeValue):
                    prop.includeOption(includeValue)
                    print("\t" * 3 + 'Include option: ' + includeValue)
                else:
                    print("\t" * 3 + 'Option already included: ' + includeValue)

            for excludeValue in excludeValues:
                if prop.isIncludedOption(excludeValue):
                    prop.excludeOption(excludeValue)
                    print("\t" * 3 + 'Exclude option: ' + excludeValue)
                else:
                    print("\t" * 3 + 'Option already excluded: ' + excludeValue)
            return

        if propValue is not None:
            try:
                prop.setValue(propValue)
                print("\t" * 3 + 'Setting value: ' + propValue)
            except Exception as exception:
                if isLockedPropertyException(exception):
                    print("\t" * 3 + 'Skipping locked-property exception: ' + propId)
                    print("\t" * 3 + 'Exception: ' + str(exception))
                    return
                raise
            return

        raise ValueError(
            "JSON property has neither value nor bitmap fields: " + propId
        )

    raise ValueError(
        "Property ID not found on module " + module.getId() + ": " + propId
    )


def checkFspVersionMismatch(requiredFspVersion, availableFspVersions):
    if requiredFspVersion.strip() in availableFspVersions:
        return SUCCESS_EXIT_CODE

    requiredParts = requiredFspVersion.strip().split(".")
    requiredMajor = int(requiredParts[0]) if len(requiredParts) > 0 else 0
    requiredMinor = int(requiredParts[1]) if len(requiredParts) > 1 else 0

    if not availableFspVersions:
        # No FSP installed at all => only Install button shown
        return FSP_MAJOR_MISMATCH_EXIT_CODE

    latestVersion = max(availableFspVersions, key=parseVersion)
    latestMajor, latestMinor, _ = parseVersion(latestVersion)

    # e2studio suggests latestVersion for Continue
    # Only safe to Continue if same major AND minor >= required
    if latestMajor == requiredMajor and latestMinor >= requiredMinor:
        return FSP_MINOR_MISMATCH_EXIT_CODE

    # Major differs or minor too low => Continue is unsafe => must Install
    return FSP_MAJOR_MISMATCH_EXIT_CODE

def parseVersion(version):
    parts = version.strip().split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major, minor, patch)

def main():
    try:
        if not configFile:
            print("[ERROR] 'configFile' argument missing")
            return FAIL_EXIT_CODE

        if not boardName:
            print("[ERROR] 'boardName' argument missing")
            return FAIL_EXIT_CODE

        if not projectName:
            print("[ERROR] 'projectName' argument missing")
            return FAIL_EXIT_CODE

        # The sensor type and the shunt type are optional because an application may have no
        # choice for one of them, but at least one of them is needed to change anything.
        if not selectedSensorType and not selectedShuntType:
            print("[ERROR] Neither 'selectedSensorType' nor 'selectedShuntType' argument is specified")
            return FAIL_EXIT_CODE

        if not familyName:
            print("[ERROR] 'familyName' argument missing")
            return FAIL_EXIT_CODE

        if not requiredFspVersion:
            print("[ERROR] 'requiredFspVersion' argument missing")
            return FAIL_EXIT_CODE

        # check FSP compatibility
        availableFspVersions = getAvailableFspVersions(familyName)
        availableVersions = [str(v).strip() for v in availableFspVersions]
        mismatchCode = checkFspVersionMismatch(requiredFspVersion, availableVersions)
        if mismatchCode != SUCCESS_EXIT_CODE:
            return mismatchCode

        if not getProject(projectName).exists():
            # import project into workspace if not already present
            projectPath = os.path.join(workspaceRoot, projectName)
            print('Importing project ' + projectName)
            importProject(projectPath)
        projectConfig = openDDSCConfiguration(projectName)

        deviceData = configData.get("deviceData")
        if not isinstance(deviceData, dict):
            raise ValueError("Config JSON lacks deviceData object")
        boardData = deviceData.get(boardName)
        if boardData is None:
            raise ValueError(
                "Board data not found: " + str(boardName)
                + "; available=" + ", ".join(deviceData.keys())
            )

        if boardData is not None:
            stackModule = boardData['motorControlModuleId']
            sensorTypes = boardData.get("deviceSensorTypes") or []
            shuntTypes = boardData.get("devicesShuntTypes") or []
            findSelectedDefinition(sensorTypes, "sensorTypeId", selectedSensorType, "sensor type")
            findSelectedDefinition(shuntTypes, "shuntTypeId", selectedShuntType, "shunt type")
            validateSensorMarkers(boardData)
            validateIncompatibleSelection(boardData)
            reportMatchingWarnings(boardData)

            # Handle Sensor Types Properties
            for sensorType in sensorTypes:
                if selectedSensorType and sensorType["sensorTypeId"] == selectedSensorType:

                    sensorTypeModules = sensorType["moduleConfigs"]
                    for sensorTypeModule in sensorTypeModules:
                        sensorTypeModuleId = sensorTypeModule["moduleId"]
                        sensorTypeParentModuleId = sensorTypeModule.get("parentModuleId")
                        sensorTypeInterfaceId = sensorTypeModule.get("interfaceId")

                        # Create or get expected stack from thread
                        if sensorTypeParentModuleId is None and sensorTypeInterfaceId is None:
                            stack = createStack(projectConfig, sensorTypeModuleId, stackModule)
                        else:
                            stack = createStackWithParent(
                                projectConfig, sensorTypeModuleId, stackModule,
                                sensorTypeParentModuleId, sensorTypeInterfaceId
                            )

                        if stack is not None:
                            print('\t' + f'Finish setting FSP stack for {stack.getId()}')
                            print('\t' + f'Stack name: {stack.getId()}')
                        if stack is None:
                            print('\t' + f'Warning: failed to create an FSP stack for {sensorTypeModuleId}')

                        # Set property to stack
                        sensorTypeModuleProperties = sensorTypeModule["properties"]
                        for sensorTypeModuleProperty in sensorTypeModuleProperties:
                            sensorTypeModulePropertyId = sensorTypeModuleProperty["propertyId"]
                            sensorTypeModulePropertyValue = sensorTypeModuleProperty.get("value")
                            sensorTypeModulePropertyIncludeValue = sensorTypeModuleProperty.get("includeValue")
                            sensorTypeModulePropertyExcludeValue = sensorTypeModuleProperty.get("excludeValue")
                            setModuleProperty(stack, sensorTypeModulePropertyId, sensorTypeModulePropertyValue,
                                              sensorTypeModulePropertyIncludeValue, sensorTypeModulePropertyExcludeValue)

            # Handle Shunt Types Properties
            for shuntType in shuntTypes:
                if selectedShuntType and shuntType["shuntTypeId"] == selectedShuntType:

                    shuntTypeModules = shuntType["moduleConfigs"]
                    for shuntTypeModule in shuntTypeModules:
                        shuntTypeModuleId = shuntTypeModule["moduleId"]
                        shuntTypeParentModuleId = shuntTypeModule.get("parentModuleId")
                        shuntTypeInterfaceId = shuntTypeModule.get("interfaceId")
                        applicableSensorTypeId = getApplicableSensorTypeId(shuntTypeModule)
                        if not isSensorSpecificModuleApplicable(shuntTypeModule):
                            print("\t" + "Skipping sensor-specific module config: " + shuntTypeModuleId + ", applicableSensorTypeId=" + str(applicableSensorTypeId) + ", selectedSensorType=" + str(selectedSensorType))
                            continue

                        # Delete stack from thread
                        operation = shuntTypeModule.get(
                            "operation",
                            "createOrUpdate"
                        )
                        if operation == "delete":
                            deleteSucceeded = deleteStackWithParent(
                                projectConfig,
                                stackModule,
                                shuntTypeParentModuleId,
                                shuntTypeInterfaceId,
                                shuntTypeModuleId
                            )
                            if not deleteSucceeded:
                                print("[ERROR] Failed to delete module: " + shuntTypeModuleId)
                                return FAIL_EXIT_CODE
                            continue

                        # Create or get expected stack from thread
                        if shuntTypeParentModuleId is None and shuntTypeInterfaceId is None:
                            stack = createStack(projectConfig, shuntTypeModuleId, stackModule)
                        elif shuntTypeParentModuleId is not None and shuntTypeInterfaceId is not None:
                            stack = createStackWithParent(projectConfig, shuntTypeModuleId, stackModule,
                                                          shuntTypeParentModuleId, shuntTypeInterfaceId)
                        else:
                            raise ValueError("parentModuleId and interfaceId must be specified together: " + shuntTypeModuleId)

                        if stack is not None:
                            print('\t' + f'Finish setting FSP stack for {stack.getId()}')
                            print('\t' + f'Stack name: {stack.getId()}')
                        if stack is None:
                            print('\t' + f'Warning: failed to create or find an FSP stack for {shuntTypeModuleId}')
                            continue

                        # Set property to stack
                        shuntTypeModuleIdProperties = shuntTypeModule["properties"]
                        for shuntTypeModuleProperty in shuntTypeModuleIdProperties:
                            shuntTypeModulePropertyId = shuntTypeModuleProperty["propertyId"]
                            shuntTypeModulePropertyValue = shuntTypeModuleProperty.get("value")
                            shuntTypeModulePropertyIncludeValue = shuntTypeModuleProperty.get("includeValue")
                            shuntTypeModulePropertyExcludeValue = shuntTypeModuleProperty.get("excludeValue")
                            setModuleProperty(stack, shuntTypeModulePropertyId, shuntTypeModulePropertyValue,
                                              shuntTypeModulePropertyIncludeValue, shuntTypeModulePropertyExcludeValue)

        projectConfig.save()
        return SUCCESS_EXIT_CODE

    except Exception as e:
        print('[FAILURE] Failed to add or delete stack:', str(e))
        print('[FAILURE] Exception type:', type(e).__name__)
        traceback.print_exc()
        return FAIL_EXIT_CODE


try:
    exit_code = main()
except SystemExit:
    pass  # allow sys.exit() silently
except:
    # Avoid unhandled exceptions from escaping to Eclipse
    print("[FAILURE] Unexpected top-level error.")
    exit_code = FAIL_EXIT_CODE
print(f"Execution time: {time.time() - start_time}s")
exit_code

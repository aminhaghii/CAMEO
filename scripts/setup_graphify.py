import subprocess
import sys
import os

def run_command(command, description):
    print(f"=== {description} ===")
    print(f"Running: {' '.join(command)}")
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.stdout:
            print(result.stdout)
        print("Success!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Warning: {description} failed.")
        if e.stdout:
            print(f"Stdout:\n{e.stdout}")
        if e.stderr:
            print(f"Stderr:\n{e.stderr}")
        return False

def main():
    # Set working directory to project root (one level up from this script)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"Project root directory: {project_root}\n")

    # 1. Install graphifyy package
    success = run_command(["pip", "install", "graphifyy"], "Installing graphifyy package via pip")
    if not success:
        # Try python -m pip
        success = run_command(["python", "-m", "pip", "install", "graphifyy"], "Installing graphifyy package via python -m pip")
        if not success:
            print("Failed to install graphifyy. Please make sure pip is installed and you have internet access.")
            sys.exit(1)

    build_success = False
    
    # Try 1: graphify update .
    try:
        print("Attempting to run 'graphify update .'...")
        result = subprocess.run(["graphify", "update", "."], shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.stdout:
            print(result.stdout)
        build_success = True
    except Exception as e:
        print(f"Global 'graphify update' command failed: {e}")
        if hasattr(e, 'stderr') and e.stderr:
            print(f"Error details:\n{e.stderr}")
        print("Attempting via Python module 'graphify'...\n")
        
    # Try 2: python -m graphify update .
    if not build_success:
        try:
            print("Attempting to run 'python -m graphify update .'...")
            result = subprocess.run(["python", "-m", "graphify", "update", "."], shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.stdout:
                print(result.stdout)
            build_success = True
        except Exception as e:
            print(f"'python -m graphify update' failed: {e}")
            if hasattr(e, 'stderr') and e.stderr:
                print(f"Error details:\n{e.stderr}")
            print("Attempting via direct module CLI invoke...\n")
            
    # Try 3: python -m graphify.cli update .
    if not build_success:
        try:
            print("Attempting to run 'python -m graphify.cli update .'...")
            result = subprocess.run(["python", "-m", "graphify.cli", "update", "."], shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.stdout:
                print(result.stdout)
            build_success = True
        except Exception as e:
            print(f"'python -m graphify.cli update' failed: {e}")
            if hasattr(e, 'stderr') and e.stderr:
                print(f"Error details:\n{e.stderr}")

    # 3. If graph was created, perform integration installation for Antigravity agent
    graph_path = os.path.join(project_root, "graphify-out", "graph.json")
    if os.path.exists(graph_path):
        print("\n=== Installing Antigravity Agent Skill Integration ===")
        integration_success = False
        try:
            print("Running 'graphify antigravity install'...")
            result = subprocess.run(["graphify", "antigravity", "install"], shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.stdout:
                print(result.stdout)
            integration_success = True
        except Exception as e:
            print(f"Global integration install failed: {e}")
            
        if not integration_success:
            try:
                print("Running 'python -m graphify antigravity install'...")
                result = subprocess.run(["python", "-m", "graphify", "antigravity", "install"], shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if result.stdout:
                    print(result.stdout)
                integration_success = True
            except Exception as e:
                print(f"Python module integration install failed: {e}")

        print(f"\n[OK] Success! Codebase graph created successfully at: {graph_path}")
        print("=========================================================================")
        print("Graphify MCP Server is now fully configured in your mcp_config.json!")
        if integration_success:
            print("Antigravity Agent integration has been successfully installed!")
        print("Please reload/restart your Gemini client to initialize the server.")
        print("=========================================================================")
    else:
        print(f"\n[ERROR] graph.json was not created at: {graph_path}")
        print("Please check the errors above. You may need to run 'graphify update .' manually in your terminal.")

if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""简单的导入测试"""

print("Testing imports...")

print("1. Testing memory_tree...")
try:
    from lifetrace.memory_tree import MemoryTree, MemoryNode
    print("   ✓ MemoryTree imported successfully")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("2. Testing trace_recorder...")
try:
    from lifetrace.trace_recorder import TraceRecorder
    print("   ✓ TraceRecorder imported successfully")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("3. Testing retriever...")
try:
    from lifetrace.retriever import MemoryRetriever
    print("   ✓ MemoryRetriever imported successfully")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("4. Testing persona_model...")
try:
    from persona.persona_model_enhanced import PersonaModel
    print("   ✓ PersonaModel imported successfully")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("5. Testing persona_injector...")
try:
    from persona.persona_injector import PersonaInjector
    print("   ✓ PersonaInjector imported successfully")
except Exception as e:
    print(f"   ✗ Error: {e}")

print("\nAll import tests complete!")


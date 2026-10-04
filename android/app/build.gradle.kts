plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.agentj.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.agentj.android"
        minSdk = 31
        targetSdk = 35
        versionCode = 1
        versionName = "0.12.0-alpha"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Test APK uses debug signing; no production key accessed.
            ndk { abiFilters += listOf("arm64-v8a") }
        }
        debug {
            applicationIdSuffix = ".debug"
            ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
    androidResources { noCompress += "onnx" }
}

dependencies {
    implementation(project(":wake"))
    implementation(files("libs/sherpa-onnx-1.13.8.aar"))
    testImplementation("junit:junit:4.13.2")
}
